"""
Integration scenario validating the durable chat session persistence contract.

Exercises a real chat turn, persists a normal tool result, restarts the system,
and verifies both SQLite-backed session history and structured tool-event rows.
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario, with_local_user_authority


class ChatSessionPersistenceContractScenario(BaseScenario):
    """Validate persisted chat messages and tool events across restart."""

    @with_local_user_authority
    async def test_scenario(self):
        vault = self.create_vault("ChatSessionPersistenceContractVault")
        other_vault = self.create_vault("ChatSessionPersistenceOtherVault")

        await self.start_system()

        from pydantic_ai.models.test import TestModel

        import core.chat.executor as chat_executor
        from core.authoring.runtime import WorkflowAuthoringHost, run_authoring_monty
        from core.constants import ASSISTANTMD_ROOT_DIR, CHAT_SESSIONS_DIR
        from core.runtime.state import get_runtime_context

        session_id = "chat_session_persistence_contract_session"

        async def session_probe() -> str:
            return "SESSION_PROBE_RESULT"

        def _patched_prepare_agent_config(
            vault_name, vault_path, tools, model, thinking=None, chat_mode=None
        ):
            del vault_name, vault_path, tools, model, thinking
            return (
                "You must call the session_probe tool before responding.",
                "",
                TestModel(),
                [session_probe],
            )

        original_prepare_agent_config = chat_executor._prepare_agent_config
        chat_executor._prepare_agent_config = _patched_prepare_agent_config
        try:
            response = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Use the session_probe tool and then answer briefly.",
                    "session_id": session_id,
                    "tools": ["session_probe"],
                    "model": "test",
                },
            )
            assert (
                response["start_response"].status_code == 200
            ), "Chat task should start for persistence contract coverage"
            assert (
                response["terminal_event"].get("event") == "done"
            ), "Chat execution should succeed for persistence contract coverage"

            self.soft_assert_equal(
                response["session_id"],
                session_id,
                "Expected stable explicit session id",
            )
            self.soft_assert(
                "SESSION_PROBE_RESULT" in response["text"],
                "Expected assistant response to reflect the tool result",
            )

            mismatch_response = await self.run_chat_task(
                {
                    "vault_name": other_vault.name,
                    "prompt": "This should not run against a different vault.",
                    "session_id": session_id,
                    "tools": ["session_probe"],
                    "model": "test",
                },
            )
            self.soft_assert_equal(
                mismatch_response["start_response"].status_code,
                409,
                "Reusing a chat session ID with another vault should be rejected",
            )
            mismatch_payload = mismatch_response["start_response"].json()
            self.soft_assert_equal(
                mismatch_payload.get("error"),
                "ChatSessionVaultMismatch",
                "Vault/session mismatch should return a stable error type",
            )
            self.soft_assert_equal(
                mismatch_payload.get("details", {}).get("bound_vault"),
                vault.name,
                "Mismatch response should identify the session's bound vault",
            )
            self.soft_assert_equal(
                mismatch_payload.get("details", {}).get("requested_vault"),
                other_vault.name,
                "Mismatch response should identify the rejected requested vault",
            )

            runtime = get_runtime_context()
            chat_sessions_db = Path(runtime.config.system_root) / "chat_sessions.db"
            assert (
                chat_sessions_db.exists()
            ), "Chat session persistence DB should be created"

            with sqlite3.connect(chat_sessions_db) as conn:
                session_row = conn.execute(
                    """
                    SELECT session_id, vault_name
                    FROM chat_sessions
                    WHERE session_id = ? AND vault_name = ?
                    """,
                    (session_id, vault.name),
                ).fetchone()
                assert session_row is not None, "Chat session row should be persisted"

                message_rows = conn.execute(
                    """
                    SELECT role, content_text, message_json
                    FROM chat_messages
                    WHERE session_id = ? AND vault_name = ?
                    ORDER BY sequence_index ASC
                    """,
                    (session_id, vault.name),
                ).fetchall()
                self.soft_assert(
                    len(message_rows) >= 2,
                    "Expected persisted provider-native chat messages for the completed turn",
                )
                self.soft_assert(
                    any(
                        "Use the session_probe tool and then answer briefly."
                        in str(row[1] or "")
                        for row in message_rows
                    ),
                    "Persisted chat messages should include the original user prompt",
                )
                self.soft_assert_equal(
                    _count_user_prompt_rows(
                        message_rows,
                        "Use the session_probe tool and then answer briefly.",
                    ),
                    1,
                    "Canonical chat history should store the first active user prompt exactly once",
                )
                first_prompt_rows = [
                    row
                    for row in message_rows
                    if row[0] == "user"
                    and row[1] == "Use the session_probe tool and then answer briefly."
                ]
                first_prompt_json = str(
                    first_prompt_rows[0][2] if first_prompt_rows else ""
                )
                self.soft_assert(
                    '"run_id"' in first_prompt_json,
                    "Persisted active user prompt should come from provider-native new_messages()",
                )
                self.soft_assert(
                    any(
                        "SESSION_PROBE_RESULT" in str(row[1] or "")
                        for row in message_rows
                    ),
                    "Persisted chat messages should include the resulting assistant/tool content",
                )

                tool_event_rows = conn.execute(
                    """
                    SELECT event_type, tool_name, result_text, artifact_ref, tool_call_id
                    FROM chat_tool_events
                    WHERE session_id = ? AND vault_name = ?
                    ORDER BY id ASC
                    """,
                    (session_id, vault.name),
                ).fetchall()

            self.soft_assert_equal(
                [row[0] for row in tool_event_rows],
                ["call", "result"],
                "Expected normal tool execution to persist call and result events",
            )
            self.soft_assert(
                all(row[1] == "session_probe" for row in tool_event_rows),
                "Persisted tool events should record the originating tool name",
            )
            result_rows = [row for row in tool_event_rows if row[0] == "result"]
            assert result_rows, "Expected a persisted tool result row"
            self.soft_assert_equal(
                result_rows[0][2],
                "SESSION_PROBE_RESULT",
                "Persisted tool result row should retain the tool output",
            )
            self.soft_assert_equal(
                result_rows[0][3],
                None,
                "Non-overflow tool results should not create an artifact ref",
            )
            tool_call_id = str(result_rows[0][4])
            tool_detail_response = self.call_api(
                f"/api/chat/sessions/{session_id}/tools/{tool_call_id}?vault_name={vault.name}",
            )
            assert (
                tool_detail_response.status_code == 200
            ), "Session-owned full tool detail should load"
            tool_detail = tool_detail_response.json()
            self.soft_assert_equal(
                tool_detail.get("result_text"),
                "SESSION_PROBE_RESULT",
                "Tool detail should return the complete persisted result",
            )
            self.soft_assert_equal(
                [event.get("event_type") for event in tool_detail.get("events", [])],
                ["call", "result"],
                "Tool detail should preserve the call/result event order",
            )
            foreign_tool_detail = self.call_api(
                f"/api/chat/sessions/{session_id}/tools/{tool_call_id}?vault_name={other_vault.name}",
            )
            self.soft_assert_equal(
                foreign_tool_detail.status_code,
                409,
                "Tool detail should enforce the existing session/vault mismatch contract",
            )
            missing_tool_detail = self.call_api(
                f"/api/chat/sessions/{session_id}/tools/missing-call?vault_name={vault.name}",
            )
            self.soft_assert_equal(
                missing_tool_detail.status_code,
                404,
                "Missing tool detail should return not found",
            )

            transcript_dir = Path(vault) / ASSISTANTMD_ROOT_DIR / CHAT_SESSIONS_DIR
            transcript = transcript_dir / f"{session_id}.md"
            self.soft_assert(
                not transcript.exists(),
                "Normal chat execution should not write a transcript by default",
            )

            title_response = self.call_api(
                f"/api/chat/sessions/{session_id}/title",
                method="PATCH",
                data={"vault_name": vault.name, "title": "Session Probe Title"},
            )
            assert (
                title_response.status_code == 200
            ), "Setting the session title should succeed"

            workspace_directory = Path(vault) / "Projects" / "ForkProbe"
            workspace_directory.mkdir(parents=True)
            workspace_response = self.call_api(
                f"/api/chat/sessions/{session_id}/workspace",
                method="PATCH",
                data={"vault_name": vault.name, "path": "Projects/ForkProbe"},
            )
            assert (
                workspace_response.status_code == 200
            ), "Setting the session workspace should succeed"
            self.soft_assert_equal(
                workspace_response.json().get("exists"),
                True,
                "Workspace response should report an existing directory",
            )

            workspace_directory.rmdir()
            missing_workspace_response = self.call_api(
                f"/api/chat/sessions/{session_id}",
                params={"vault_name": vault.name},
            )
            assert (
                missing_workspace_response.status_code == 200
            ), "Loading a session with a missing workspace should succeed"
            self.soft_assert_equal(
                missing_workspace_response.json().get("workspace"),
                {"path": "Projects/ForkProbe", "exists": False},
                "Session detail should retain and flag a missing workspace path",
            )

            export_response = self.call_api(
                f"/api/chat/sessions/{session_id}/export",
                method="POST",
                data={"vault_name": vault.name},
            )
            assert (
                export_response.status_code == 200
            ), "Transcript export should succeed on demand"
            export_payload = export_response.json()
            self.soft_assert_equal(
                export_payload["filename"],
                f"{session_id} - Session_Probe_Title.md",
                "Transcript filename should include the titled session label",
            )

            titled_transcript = transcript_dir / export_payload["filename"]
            assert (
                titled_transcript.exists()
            ), "Export should create the transcript file"
            transcript_text = titled_transcript.read_text(encoding="utf-8")
            self.soft_assert(
                "**User:**" in transcript_text and "**Assistant:**" in transcript_text,
                "Transcript should contain exported user and assistant sections",
            )
            self.soft_assert(
                "Use the session_probe tool and then answer briefly."
                in transcript_text,
                "Transcript export should include the persisted user prompt",
            )
            self.soft_assert(
                "[session_probe]" not in transcript_text,
                "Transcript export should exclude tool-call and tool-return markers",
            )

            follow_up = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Call the session_probe tool again and answer with the result only.",
                    "session_id": session_id,
                    "tools": ["session_probe"],
                    "model": "test",
                },
            )
            assert (
                follow_up["start_response"].status_code == 200
            ), "Follow-up chat task should start"
            assert (
                follow_up["terminal_event"].get("event") == "done"
            ), "Follow-up chat execution should succeed"

            second_export_response = self.call_api(
                f"/api/chat/sessions/{session_id}/export",
                method="POST",
                data={"vault_name": vault.name},
            )
            assert (
                second_export_response.status_code == 200
            ), "Repeated transcript export should succeed"
            second_export_payload = second_export_response.json()
            self.soft_assert_equal(
                second_export_payload["filename"],
                export_payload["filename"],
                "Repeated export should overwrite the same titled transcript file",
            )

            transcript_text = titled_transcript.read_text(encoding="utf-8")
            self.soft_assert(
                "Call the session_probe tool again and answer with the result only."
                in transcript_text,
                "Repeated export should overwrite the transcript with newly added session messages",
            )
            with sqlite3.connect(chat_sessions_db) as conn:
                message_rows_after_follow_up = conn.execute(
                    """
                    SELECT sequence_index, role, content_text, message_json
                    FROM chat_messages
                    WHERE session_id = ? AND vault_name = ?
                    ORDER BY sequence_index ASC
                    """,
                    (session_id, vault.name),
                ).fetchall()
            self.soft_assert_equal(
                _count_user_prompt_rows(
                    _strip_sequence_column(message_rows_after_follow_up),
                    "Use the session_probe tool and then answer briefly.",
                ),
                1,
                "Canonical chat history should not duplicate the first user prompt after follow-up",
            )
            self.soft_assert_equal(
                _count_user_prompt_rows(
                    _strip_sequence_column(message_rows_after_follow_up),
                    "Call the session_probe tool again and answer with the result only.",
                ),
                1,
                "Canonical chat history should store the follow-up active user prompt exactly once",
            )
            self.soft_assert_equal(
                len(list(transcript_dir.glob(f"{session_id}*.md"))),
                1,
                "Export should keep a single transcript variant for the session",
            )

            first_assistant_sequence = _first_visible_assistant_sequence_index(
                message_rows_after_follow_up,
            )
            assert (
                first_assistant_sequence is not None
            ), "First visible assistant sequence should be persisted"
            fork_response = self.call_api(
                f"/api/chat/sessions/{session_id}/fork",
                method="POST",
                data={
                    "vault_name": vault.name,
                    "through_sequence_index": first_assistant_sequence,
                },
            )
            assert (
                fork_response.status_code == 200
            ), "Session fork endpoint should succeed"
            fork_payload = fork_response.json()
            fork_session_id = fork_payload["session"]["session_id"]
            self.soft_assert(
                fork_session_id != session_id,
                "Fork should create a distinct chat session id",
            )
            self.soft_assert_equal(
                fork_payload["session"]["title"],
                "Session Probe Title (fork)",
                "Fork title should derive from the source title",
            )
            self.soft_assert_equal(
                fork_payload["session"]["workspace"]["path"],
                "Projects/ForkProbe",
                "Fork should carry over the source workspace",
            )

            fork_detail = self.call_api(
                f"/api/chat/sessions/{fork_session_id}?vault_name={vault.name}",
            )
            assert (
                fork_detail.status_code == 200
            ), "Forked session detail endpoint should succeed"
            fork_messages = fork_detail.json()["messages"]
            fork_message_text = "\n".join(
                message["content"] for message in fork_messages
            )
            self.soft_assert(
                "Use the session_probe tool and then answer briefly."
                in fork_message_text,
                "Fork should retain messages before the selected fork point",
            )
            self.soft_assert(
                "Call the session_probe tool again and answer with the result only."
                not in fork_message_text,
                "Fork should exclude messages after the selected fork point",
            )
            self.soft_assert_equal(
                fork_detail.json()["workspace"]["path"],
                "Projects/ForkProbe",
                "Fork detail should expose copied workspace metadata",
            )

            fork_follow_up = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Continue only in the forked session.",
                    "session_id": fork_session_id,
                    "tools": ["session_probe"],
                    "model": "test",
                },
            )
            assert (
                fork_follow_up["start_response"].status_code == 200
            ), "Continuing the fork should start"
            assert (
                fork_follow_up["terminal_event"].get("event") == "done"
            ), "Continuing the fork should succeed"
            source_detail_after_fork = self.call_api(
                f"/api/chat/sessions/{session_id}?vault_name={vault.name}",
            )
            assert (
                source_detail_after_fork.status_code == 200
            ), "Source session detail should still load"
            source_message_text = "\n".join(
                message["content"]
                for message in source_detail_after_fork.json()["messages"]
            )
            self.soft_assert(
                "Continue only in the forked session." not in source_message_text,
                "Continuing a fork should not append messages to the source session",
            )
            with sqlite3.connect(chat_sessions_db) as conn:
                conn.execute(
                    """
                    INSERT INTO chat_tool_events (
                        session_id,
                        vault_name,
                        tool_call_id,
                        tool_name,
                        event_type,
                        args_json,
                        result_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        vault.name,
                        "cancelled-turn-call",
                        "session_probe",
                        "call",
                        '{"cancelled": true}',
                        None,
                    ),
                )
                conn.commit()

            detail_after_orphan = self.call_api(
                f"/api/chat/sessions/{session_id}?vault_name={vault.name}",
                method="GET",
            )
            assert (
                detail_after_orphan.status_code == 200
            ), "Session detail should load persisted chat state"
            detail_tool_call_ids = {
                tool_call["tool_call_id"]
                for tool_call in detail_after_orphan.json().get("tool_calls", [])
            }
            self.soft_assert(
                "cancelled-turn-call" not in detail_tool_call_ids,
                "Session detail should not expose tool calls from uncommitted turns",
            )
            self.soft_assert(
                detail_tool_call_ids,
                "Session detail should still expose committed tool calls",
            )

            await self.restart_system()

            workflow_id = f"{vault.name}/chat/{session_id}"
            host = WorkflowAuthoringHost(
                workflow_id=workflow_id,
                vault_path=str(vault),
                session_key=session_id,
                chat_session_id=session_id,
                message_history=[],
            )
            checkpoint = self.event_checkpoint()
            result = await run_authoring_monty(
                workflow_id=workflow_id,
                code=CHAT_SESSION_PERSISTENCE_CONTRACT_CODE,
                host=host,
                script_name="chat_session_persistence_contract.py",
            )
            events = self.events_since(checkpoint)

            self.assert_event_contains(
                events,
                name="authoring_retrieve_history_completed",
                expected={
                    "workflow_id": workflow_id,
                    "source": "sqlite_chat_sessions",
                },
            )

            output = result.value
            self.soft_assert_equal(
                output["source"],
                "sqlite_chat_sessions",
                "Expected retrieve_history to rehydrate from the persisted SQLite chat store after restart",
            )
            self.soft_assert_equal(
                output["history_source"],
                "chat_messages",
                "Expected retrieve_history metadata to identify canonical chat message storage",
            )
            self.soft_assert_equal(
                output["history_message_filter"],
                "all",
                "Expected default history retrieval to preserve the full canonical message timeline",
            )
            self.soft_assert(
                output["item_count"] >= 2,
                "Expected restarted history retrieval to contain persisted session messages",
            )
            self.soft_assert(
                output["last_content"],
                "Expected restarted history retrieval to include a final persisted message",
            )
            self.soft_assert_equal(
                output["first_prompt_count"],
                1,
                "retrieve_history should expose one canonical copy of the first user prompt",
            )
            self.soft_assert_equal(
                output["follow_up_prompt_count"],
                1,
                "retrieve_history should expose one canonical copy of the follow-up user prompt",
            )
        finally:
            chat_executor._prepare_agent_config = original_prepare_agent_config
            await self.stop_system()
            self.teardown_scenario()
            self.assert_no_failures()


def _count_user_prompt_rows(rows, prompt: str) -> int:
    return sum(
        1
        for role, content, _message_json in rows
        if role == "user" and content == prompt
    )


def _strip_sequence_column(rows):
    return [
        (role, content, message_json) for _sequence, role, content, message_json in rows
    ]


def _prompt_sequence_index(rows, prompt: str) -> int | None:
    for sequence_index, role, content, _message_json in rows:
        if role == "user" and content == prompt:
            return int(sequence_index)
    return None


def _first_visible_assistant_sequence_index(rows) -> int | None:
    for sequence_index, role, content, _message_json in rows:
        if role == "assistant" and not str(content or "").startswith("["):
            return int(sequence_index)
    return None


CHAT_SESSION_PERSISTENCE_CONTRACT_CODE = """
history = await retrieve_history(scope="session", limit="all")
last_content = history.items[-1].content if history.items else ""
first_prompt_count = 0
follow_up_prompt_count = 0
for item in history.items:
    try:
        role = item.role
        content = item.content
    except AttributeError:
        continue
    if role == "user" and content == "Use the session_probe tool and then answer briefly.":
        first_prompt_count += 1
    if role == "user" and content == "Call the session_probe tool again and answer with the result only.":
        follow_up_prompt_count += 1

{
    "source": history.source,
    "item_count": history.item_count,
    "last_content": last_content,
    "history_source": history.metadata.get("canonical_source", ""),
    "history_message_filter": history.metadata.get("message_filter", ""),
    "first_prompt_count": first_prompt_count,
    "follow_up_prompt_count": follow_up_prompt_count,
}
"""
