"""Integration scenario for deterministic persisted chat tool replay."""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from validation.core.base_scenario import BaseScenario


class ChatToolReplayContractScenario(BaseScenario):
    """Validate persisted tool replay is keyed by committed tool call IDs."""

    async def test_scenario(self):
        vault = self.create_vault("ChatToolReplayContractVault")

        await self.start_system()

        from core.chat.chat_store import ChatStore
        from core.chat.compaction import build_compaction_summary_message
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()
        store = ChatStore(system_root=str(runtime.config.system_root))
        session_id = "chat_tool_replay_contract_session"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )

        store.add_messages(
            session_id,
            vault.name,
            [
                ModelRequest(parts=[UserPromptPart(content="Use both probe tools.")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="probe_alpha",
                            args={"path": "A.md"},
                            tool_call_id="probe-a",
                        ),
                        ToolCallPart(
                            tool_name="probe_beta",
                            args={"path": "B.md"},
                            tool_call_id="probe-b",
                        ),
                        ToolCallPart(
                            tool_name="probe_gamma",
                            args={"path": "C.md"},
                            tool_call_id="probe-c",
                        ),
                    ],
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="probe_alpha",
                            content="alpha result",
                            tool_call_id="probe-a",
                        ),
                        ToolReturnPart(
                            tool_name="probe_beta",
                            content="beta result",
                            tool_call_id="probe-b",
                        ),
                        ToolReturnPart(
                            tool_name="probe_gamma",
                            content="gamma interrupted",
                            tool_call_id="probe-c",
                        ),
                    ],
                ),
                ModelResponse(parts=[TextPart(content="Both probes completed.")]),
            ],
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="probe-c",
            tool_name="probe_gamma",
            event_type="call",
            args={"path": "C.md"},
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="probe-c",
            tool_name="probe_gamma",
            event_type="result",
            result_text="gamma interrupted",
            result_metadata={"status": "cancelled", "token_count": 4},
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="cancelled-call",
            tool_name="probe_cancelled",
            event_type="call",
            args={"path": "cancelled.md"},
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="probe-b",
            tool_name="probe_beta",
            event_type="call",
            args={"path": "B.md"},
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="probe-b",
            tool_name="probe_beta",
            event_type="result",
            result_text="beta result",
            result_metadata={
                "status": "denied",
                "failure_kind": "probe_failure",
                "token_count": 2,
            },
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="probe-a",
            tool_name="probe_alpha",
            event_type="call",
            args={"path": "A.md"},
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="probe-a",
            tool_name="probe_alpha",
            event_type="result",
            result_text="alpha result",
            result_metadata={
                "fetched_at": datetime(2026, 8, 9, 1, 2, tzinfo=UTC),
                "token_count": 3,
            },
        )

        detail_response = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}",
        )
        assert detail_response.status_code == 200, "Session detail should load"
        detail = detail_response.json()

        message_payloads = detail["messages"]
        tool_call_message = next(
            message
            for message in message_payloads
            if set(message["tool_call_ids"]) == {"probe-a", "probe-b", "probe-c"}
        )
        tool_return_message = next(
            message
            for message in message_payloads
            if set(message["tool_return_ids"]) == {"probe-a", "probe-b", "probe-c"}
        )
        self.soft_assert(
            tool_call_message["is_tool_message"],
            "Tool-call message should be marked as a tool message",
        )
        self.soft_assert(
            tool_return_message["is_tool_message"],
            "Tool-return message should be marked as a tool message",
        )
        self.soft_assert_equal(
            tool_call_message["tool_call_ids"],
            ["probe-a", "probe-b", "probe-c"],
            "Tool-call IDs should preserve Pydantic message part order",
        )
        self.soft_assert_equal(
            tool_return_message["tool_return_ids"],
            ["probe-a", "probe-b", "probe-c"],
            "Tool-return IDs should preserve Pydantic message part order",
        )
        self.soft_assert_equal(
            tool_call_message["content"],
            "",
            "Session detail should withhold tool-call arguments from message rows",
        )
        self.soft_assert_equal(
            tool_return_message["content"],
            "",
            "Session detail should withhold tool results from message rows",
        )

        tool_calls = detail["tool_calls"]
        event_ids = [tool_call["tool_call_id"] for tool_call in tool_calls]
        self.soft_assert(
            "cancelled-call" not in event_ids,
            "Session detail should omit orphan diagnostic tool events",
        )
        self.soft_assert_equal(
            event_ids,
            ["probe-a", "probe-b", "probe-c"],
            "Session detail should preserve effective message order for tool calls",
        )
        self.soft_assert_equal(
            tool_calls,
            [
                {
                    "tool_call_id": "probe-a",
                    "tool_name": "probe_alpha",
                    "status": "completed",
                    "token_count": 3,
                },
                {
                    "tool_call_id": "probe-b",
                    "tool_name": "probe_beta",
                    "status": "failed",
                    "token_count": 2,
                },
                {
                    "tool_call_id": "probe-c",
                    "tool_name": "probe_gamma",
                    "status": "interrupted",
                    "token_count": 4,
                },
            ],
            "Session detail should expose only safe tool identity, lifecycle, and size metadata",
        )
        self.soft_assert(
            "tool_events" not in detail,
            "Session detail should not eagerly expose persisted tool-event contents",
        )

        alpha_detail_response = self.call_api(
            f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            alpha_detail_response.status_code,
            200,
            "Explicit tool detail should load",
        )
        alpha_detail = alpha_detail_response.json()
        self.soft_assert_equal(
            alpha_detail["args"],
            {"path": "A.md"},
            "Explicit tool detail should contain complete arguments",
        )
        self.soft_assert_equal(
            alpha_detail["result_text"],
            "alpha result",
            "Explicit tool detail should contain the complete result",
        )
        self.soft_assert_equal(
            alpha_detail["result_metadata"]["fetched_at"],
            "2026-08-09T01:02:00Z",
            "Explicit detail should preserve normalized result metadata",
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="probe_deferred",
                            args={"path": "deferred-generation.md"},
                            tool_call_id="probe-a",
                        )
                    ]
                )
            ],
        )
        effective_collision = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            [call["tool_call_id"] for call in effective_collision.json()["tool_calls"]],
            ["probe-b", "probe-c"],
            "A reused unreturned ID should also hide an earlier effective generation",
        )
        effective_collision_detail = self.call_api(
            f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            effective_collision_detail.status_code,
            404,
            "A reused unexecuted ID should not alias earlier effective tool detail",
        )

        summary_message = build_compaction_summary_message(
            "The probe work was completed before compaction."
        )
        store.add_compaction_checkpoint(
            session_id=session_id,
            vault_name=vault.name,
            checkpoint_id="tool-replay-compaction",
            source="validation",
            message_count_before=5,
            last_message_sequence_index=4,
            summary_message=summary_message,
            replacement_history=[summary_message],
        )
        compacted_detail = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            compacted_detail.json()["tool_calls"],
            [],
            "Session detail should not send archived tool calls after compaction",
        )
        archived_detail = self.call_api(
            f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            archived_detail.status_code,
            404,
            "Ordinary tool detail should not expose calls outside effective history",
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="probe_reused",
                            args={"path": "new-generation.md"},
                            tool_call_id="probe-a",
                        )
                    ]
                )
            ],
        )
        deferred_collision_session = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            deferred_collision_session.json()["tool_calls"],
            [],
            "An unreturned effective call should not match archived event rows",
        )
        deferred_collision_detail = self.call_api(
            f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            deferred_collision_detail.status_code,
            404,
            "An unexecuted reused ID should not unlock archived tool detail",
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="probe_reused",
                            content="new generation result",
                            tool_call_id="probe-a",
                        )
                    ]
                ),
                ModelResponse(parts=[TextPart(content="Reused probe completed.")]),
            ],
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="probe-a",
            tool_name="probe_reused",
            event_type="call",
            args={"path": "new-generation.md"},
        )
        store.add_tool_event(
            session_id=session_id,
            vault_name=vault.name,
            tool_call_id="probe-a",
            tool_name="probe_reused",
            event_type="result",
            result_text="new generation result",
        )
        collision_session = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            collision_session.json()["tool_calls"],
            [],
            "Ambiguous reused tool-call IDs should be withheld from summaries",
        )
        collision_detail = self.call_api(
            f"/api/chat/sessions/{session_id}/tools/probe-a?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            collision_detail.status_code,
            404,
            "A reused effective ID should not unlock mixed-generation tool detail",
        )

        duplicate_event_session = "chat_tool_duplicate_event_session"
        store.ensure_session(
            duplicate_event_session,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            duplicate_event_session,
            vault.name,
            [
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="probe_duplicate_event",
                            args={"path": "single-declaration.md"},
                            tool_call_id="duplicate-event-call",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="probe_duplicate_event",
                            content="duplicate event result",
                            tool_call_id="duplicate-event-call",
                        )
                    ]
                ),
            ],
        )
        for path in ("first-event.md", "second-event.md"):
            store.add_tool_event(
                session_id=duplicate_event_session,
                vault_name=vault.name,
                tool_call_id="duplicate-event-call",
                tool_name="probe_duplicate_event",
                event_type="call",
                args={"path": path},
            )
        store.add_tool_event(
            session_id=duplicate_event_session,
            vault_name=vault.name,
            tool_call_id="duplicate-event-call",
            tool_name="probe_duplicate_event",
            event_type="result",
            result_text="duplicate event result",
        )
        duplicate_event_summary = self.call_api(
            f"/api/chat/sessions/{duplicate_event_session}?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            duplicate_event_summary.json()["tool_calls"],
            [],
            "Duplicate stored call rows should be withheld independently",
        )
        duplicate_event_detail = self.call_api(
            f"/api/chat/sessions/{duplicate_event_session}/tools/duplicate-event-call"
            f"?vault_name={vault.name}",
        )
        self.soft_assert_equal(
            duplicate_event_detail.status_code,
            404,
            "Duplicate stored call rows should make detail unavailable",
        )
        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
