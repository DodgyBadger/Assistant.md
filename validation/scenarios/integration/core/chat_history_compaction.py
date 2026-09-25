"""Integration scenario for chat history compaction primitives and API."""

import json
import sqlite3
import sys
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


class ChatHistoryCompactionScenario(BaseScenario):
    """Validate compaction rewrites stored history safely."""

    async def test_scenario(self):
        vault = self.create_vault("ChatHistoryCompactionVault")

        await self.start_system()

        import core.chat.compaction as compaction
        import core.llm.agents as llm_agents
        import core.tools.session_ops as session_ops
        from core.chat.chat_store import ChatStore
        from core.chat.history_service import ChatHistoryContext, ChatHistoryService
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()
        store = ChatStore(system_root=str(runtime.config.system_root))
        session_id = "chat_history_compaction_session"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        messages = [
            ModelRequest(parts=[UserPromptPart(content="First user decision.")]),
            ModelResponse(parts=[TextPart(content="First assistant answer.")]),
            ModelRequest(parts=[UserPromptPart(content="Please use the probe tool.")]),
            ModelResponse(
                parts=[ToolCallPart(tool_name="probe", args={}, tool_call_id="probe-1")]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="probe",
                        content="probe result",
                        tool_call_id="probe-1",
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="Probe result handled.")]),
        ]
        store.add_messages(session_id, vault.name, messages)
        assert (
            store.get_message_count(session_id, vault.name, mode="raw") == 6
        ), "Raw count starts with seeded messages"
        assert (
            store.get_message_count(session_id, vault.name) == 6
        ), "Effective count matches raw count before compaction"
        assert (
            store.get_session_history_revision(session_id, vault.name) == 1
        ), "Initial raw message append advances session history revision"

        older_messages, recent_messages = compaction.split_history_for_compaction(
            messages,
            keep_recent=2,
        )
        prompt = compaction._build_summary_prompt(
            older_messages=older_messages,
            recent_messages=recent_messages,
            focus="Keep decisions and tool outcomes.",
        )
        prompt_payload = json.loads(prompt)
        assert (
            prompt_payload["prompt_contract_version"] == "recovery-card-v3"
        ), "Compaction prompt declares the summary contract version"
        assert (
            "context checkpoint compaction" in prompt
        ), "Compaction prompt frames the summary as resumable task state"
        assert (
            "Open tasks, unresolved questions, blockers, risks" in prompt
        ), "Compaction prompt asks for recovery-card continuation details"
        assert (
            "operational requests, not task objectives" in prompt
        ), "Compaction prompt should not treat session hygiene as the current objective"
        older_prompt_text = json.dumps(
            prompt_payload["older_history"], ensure_ascii=False
        )
        recent_prompt_text = json.dumps(
            prompt_payload["retained_recent_history"],
            ensure_ascii=False,
        )
        assert (
            "probe result" not in older_prompt_text
        ), "Compaction prompt should not treat recent turns as history to compress"
        assert (
            "probe result" in recent_prompt_text
        ), "Compaction prompt should provide retained recent turns for supersession checks"
        assert (
            len(recent_messages) == 3
        ), "Recent slice shifts backward to preserve tool pair"

        shaped_prompt = compaction._build_summary_prompt(
            older_messages=[
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="empty_probe",
                            content="",
                            tool_call_id="empty-1",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="failed_probe",
                            content="long failure details should not enter compaction prompt",
                            tool_call_id="failed-1",
                            outcome="failed",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="large_success_probe",
                            content="large successful result " * 100,
                            tool_call_id="success-1",
                        )
                    ]
                ),
            ],
            focus=None,
        )
        assert (
            "[tool result omitted] empty_probe: empty result" in shaped_prompt
        ), "Empty tool results should be omitted from compaction prompt input"
        assert (
            "long failure details should not enter compaction prompt"
            not in shaped_prompt
        ), "Explicit failed tool-result content should be omitted from compaction prompt input"
        assert (
            shaped_prompt.count("large successful result") == 100
        ), "Successful retained tool results should not be truncated by first-slice shaping"

        class _StreamingSummaryResult:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def stream_output(self, *, debounce_by=0.1):
                assert (
                    debounce_by is None
                ), "Background model runs should consume the stream fully"
                yield "Streamed "
                yield "Streamed compaction summary."

            async def get_output(self):
                return "Streamed compaction summary."

            def all_messages(self):
                return []

        class _StreamingSummaryAgent:
            async def run(self, *args, **kwargs):
                raise AssertionError(
                    "Compaction summary generation must use streaming model calls"
                )

            def run_stream(self, *args, **kwargs):
                return _StreamingSummaryResult()

        async def _create_streaming_summary_agent_stub(*args, **kwargs):
            return _StreamingSummaryAgent()

        original_create_agent = llm_agents.create_agent
        llm_agents.create_agent = _create_streaming_summary_agent_stub
        try:
            streamed_summary = await compaction._generate_compaction_summary(
                older_messages=older_messages,
                recent_messages=recent_messages,
                focus="Keep decisions and tool outcomes.",
            )
        finally:
            llm_agents.create_agent = original_create_agent

        assert (
            streamed_summary == "Streamed compaction summary."
        ), "Compaction summary generation should return aggregated streamed text"

        original_keep_recent = compaction.get_compaction_keep_recent
        original_threshold = compaction.get_compaction_token_threshold
        compaction.get_compaction_keep_recent = lambda: 2
        compaction.get_compaction_token_threshold = lambda: 1

        try:
            status_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compaction-status?vault_name={vault.name}"
            )
            assert (
                status_response.status_code == 200
            ), "Compaction status endpoint succeeds"
            status_payload = status_response.json()
            assert (
                status_payload["messages_before"] == 6
            ), "Status reports current message count"
            assert (
                status_payload["recommended"] is True
            ), "Status recommends compaction past threshold"
            assert (
                "export_recommended" not in status_payload
            ), "Status response omits export fields"

            async def _summary_stub(*args, **kwargs):
                return "Preserve first decision and the probe result outcome."

            original_generate_summary = compaction._generate_compaction_summary
            compaction._generate_compaction_summary = _summary_stub
            try:
                compact_response = self.call_api(
                    f"/api/chat/sessions/{session_id}/compact",
                    method="POST",
                    data={
                        "vault_name": vault.name,
                        "focus": "Keep decisions and tool outcomes.",
                    },
                )
                second_compact_response = self.call_api(
                    f"/api/chat/sessions/{session_id}/compact",
                    method="POST",
                    data={
                        "vault_name": vault.name,
                        "focus": "Keep the compacted decision and current tool outcome.",
                    },
                )
            finally:
                compaction._generate_compaction_summary = original_generate_summary
        finally:
            compaction.get_compaction_keep_recent = original_keep_recent
            compaction.get_compaction_token_threshold = original_threshold

        assert compact_response.status_code == 200, "Compaction endpoint succeeds"
        compact_payload = compact_response.json()
        assert (
            compact_payload["messages_before"] == 6
        ), "Compaction reports original count"
        assert (
            compact_payload["messages_after"] == 4
        ), "Compaction keeps summary plus adjusted recent slice"
        assert (
            compact_payload["kept_recent"] == 3
        ), "Recent slice shifts backward to preserve tool pair"
        assert (
            "export_recommended" not in compact_payload
        ), "Compaction response omits export fields"
        assert (
            "export_created" not in compact_payload
        ), "Compaction response omits export fields"
        assert (
            "export_path" not in compact_payload
        ), "Compaction response omits export fields"

        raw_messages = store.get_stored_messages(session_id, vault.name, mode="raw")
        assert (
            len(raw_messages) == 6
        ), "Compaction preserves original raw chat_messages rows"
        assert (
            raw_messages[0].content_text == "First user decision."
        ), "Raw archival history still includes pre-compaction messages"

        effective_messages = store.get_stored_messages(session_id, vault.name)
        assert (
            len(effective_messages) == 4
        ), "Default stored-message reads return effective history"
        assert (
            effective_messages[0].role == "system"
        ), "Effective history starts with summary"
        assert (
            "AssistantMD compacted chat history" in effective_messages[0].content_text
        ), "Effective summary marker is reconstructed from checkpoint"
        assert effective_messages[1].content_text.startswith(
            "[probe] (tool call)"
        ), "Effective history preserves recent tool call"
        assert (
            "probe result" in effective_messages[2].content_text
        ), "Effective history preserves recent tool result"
        provider_history = store.get_history(session_id, vault.name)
        assert (
            provider_history is not None and len(provider_history) == 4
        ), "Provider-native history defaults to effective replay"

        detail = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
        )
        assert (
            detail.status_code == 200
        ), "Session detail endpoint succeeds after compaction"
        detail_messages = detail.json()["messages"]
        assert len(detail_messages) == 4, "Session detail shows effective history"
        assert (
            detail_messages[0]["role"] == "system"
        ), "First message is system-maintained summary"
        assert (
            "AssistantMD compacted chat history" in detail_messages[0]["content"]
        ), "Summary marker is exposed through effective replay"
        assert (
            detail_messages[1]["is_tool_message"] is True
            and detail_messages[1]["tool_call_ids"] == ["probe-1"]
            and detail_messages[1]["content"] == ""
        ), "Tool call remains in recent history without exposing its content"
        assert (
            detail_messages[2]["is_tool_message"] is True
            and detail_messages[2]["tool_return_ids"] == ["probe-1"]
            and detail_messages[2]["content"] == ""
        ), "Tool result remains paired with the call without exposing its content"
        assert [message["fork_sequence_index"] for message in detail_messages] == [
            0,
            1,
            2,
            3,
        ], "Compacted replacement messages expose effective fork points"
        fork_response = self.call_api(
            f"/api/chat/sessions/{session_id}/fork",
            method="POST",
            data={
                "vault_name": vault.name,
                "through_sequence_index": detail_messages[-1]["fork_sequence_index"],
            },
        )
        assert (
            fork_response.status_code == 200
        ), "Forking from compacted retained messages succeeds"
        fork_payload = fork_response.json()
        assert (
            fork_payload["copied_message_count"] == 4
        ), "Forking from compacted replacement history copies only the visible effective prefix"
        fork_session_id = fork_payload["session"]["session_id"]
        fork_detail = self.call_api(
            f"/api/chat/sessions/{fork_session_id}?vault_name={vault.name}"
        )
        assert fork_detail.status_code == 200, "Forked compacted session detail loads"
        fork_messages = fork_detail.json()["messages"]
        assert (
            len(fork_messages) == 4
        ), "Forked session starts from the compacted visible history"
        assert (
            fork_messages[0]["role"] == "system"
        ), "Forked session preserves the compaction card as a system-maintained message"
        assert (
            "AssistantMD compacted chat history" in fork_messages[0]["content"]
        ), "Forked session keeps the compaction card as its starting context"
        assert (
            fork_messages[-1]["content"] == "Probe result handled."
        ), "Forked session includes the retained assistant message without restoring archival history"
        fork_metadata = store.get_session_metadata(fork_session_id, vault.name)
        assert "fork" in fork_metadata, "Forked session records fork provenance"
        assert (
            "last_compaction" not in fork_metadata
        ), "Forked compacted session should not inherit source checkpoint metadata"
        assert (
            fork_metadata["history_revision"] == 1
        ), "Forked compacted session starts a fresh history revision from copied effective history"
        metadata = store.get_session_metadata(session_id, vault.name)
        assert "last_compaction" in metadata, "Compaction audit metadata is recorded"
        assert (
            metadata["last_compaction"]["prompt_contract_version"] == "recovery-card-v3"
        ), "Session metadata records the compaction prompt contract"
        assert (
            metadata["last_compaction"]["trigger"] == "manual"
        ), "API compaction is recorded as a manual trigger"
        assert (
            metadata["last_compaction"]["reason"] == "api_requested"
        ), "API compaction records the manual reason"
        assert (
            metadata["last_compaction"]["compaction_keep_recent"] == 2
        ), "Session metadata records effective compaction settings"

        checkpoint = store.get_latest_compaction_checkpoint(session_id, vault.name)
        assert checkpoint is not None, "Compaction records a replay checkpoint"
        assert (
            checkpoint.last_message_sequence_index == 5
        ), "Checkpoint records the raw message high-water mark"
        checkpoint_metadata = json.loads(checkpoint.metadata_json or "{}")
        assert (
            checkpoint_metadata["prompt_contract_version"] == "recovery-card-v3"
        ), "Checkpoint metadata records the prompt contract version"
        assert (
            checkpoint_metadata["trigger"] == "manual"
        ), "Checkpoint metadata records manual/API compaction trigger"
        assert (
            checkpoint_metadata["reason"] == "api_requested"
        ), "Checkpoint metadata records why compaction ran"

        migration_conn = sqlite3.connect(
            runtime.config.system_root / "chat_sessions.db"
        )
        try:
            migration_rows = migration_conn.execute(
                """
                SELECT version
                FROM schema_migrations
                WHERE namespace = 'chat_sessions'
                ORDER BY version
                """
            ).fetchall()
        finally:
            migration_conn.close()
        assert [row[0] for row in migration_rows] == [
            1,
            2,
            3,
        ], "Chat migrations are recorded in schema_migrations"

        assert (
            second_compact_response.status_code == 200
        ), "Second compaction endpoint succeeds"
        second_payload = second_compact_response.json()
        assert (
            second_payload["messages_before"] == 4
        ), "Second compaction reads latest effective history, not raw archival history"
        assert (
            store.get_message_count(session_id, vault.name, mode="raw") == 6
        ), "Second compaction still preserves raw rows"
        assert (
            store.get_message_count(session_id, vault.name) == 4
        ), "Second compaction keeps default effective history compacted"
        assert (
            store.get_session_history_revision(session_id, vault.name) == 3
        ), "Each compaction advances session history revision"

        store.add_messages(
            session_id,
            vault.name,
            [
                ModelRequest(
                    parts=[UserPromptPart(content="Post-compaction follow-up.")]
                )
            ],
        )
        assert (
            store.get_message_count(session_id, vault.name, mode="raw") == 7
        ), "Post-compaction turns append to raw history"
        assert (
            store.get_session_history_revision(session_id, vault.name) == 4
        ), "Post-compaction raw append advances session history revision"
        replay_messages = store.get_stored_messages(session_id, vault.name)
        assert (
            len(replay_messages) == 5
        ), "Effective replay includes latest checkpoint replacement plus appended raw turn"
        assert (
            replay_messages[-1].content_text == "Post-compaction follow-up."
        ), "Post-checkpoint raw message appears after checkpoint replacement"
        post_detail = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
        )
        assert (
            post_detail.status_code == 200
        ), "Session detail endpoint succeeds after post-checkpoint append"
        post_detail_messages = post_detail.json()["messages"]
        assert (
            post_detail_messages[-1]["content"] == "Post-compaction follow-up."
        ), "Session detail exposes post-checkpoint raw messages"
        assert (
            post_detail_messages[-1]["fork_sequence_index"] == 6
        ), "Post-checkpoint raw messages keep their own raw fork point"

        broker_history = ChatHistoryService(chat_store=store).get_conversation_history(
            context=ChatHistoryContext(session_id=session_id, vault_name=vault.name),
            scope="session",
            session_id=session_id,
            limit="all",
        )
        assert (
            broker_history.item_count == 5
        ), "History broker returns effective history after compaction"
        assert all(
            item.content != "First user decision." for item in broker_history.items
        ), "History broker does not expose pre-checkpoint raw messages by default"

        class _SummaryAgentStub:
            def __init__(self, output_type):
                self.output_type = output_type

        async def _create_summary_agent_stub(*, output_type=None, **kwargs):
            return _SummaryAgentStub(output_type)

        def _build_model_stub(model_name):
            return model_name

        async def _generate_summary_response_stub(agent, prompt, **kwargs):
            if agent.output_type is session_ops._SessionSummaryIntent:
                assert (
                    "AssistantMD compacted chat history" in prompt
                ), "Session summarization prompt should include effective checkpoint summary"
                assert (
                    "primary source of durable session substance" in prompt
                ), "Session summarization prompt should prioritize the compaction card"
                assert (
                    "Post-compaction follow-up." in prompt
                ), "Session summarization prompt should include post-checkpoint raw turns"
                assert (
                    "First user decision." not in prompt
                ), "Session summarization prompt should not include archival pre-checkpoint raw history"
                return agent.output_type(
                    summary="Effective summary prompt only.",
                    user_intent="Validate effective summarization.",
                )
            if agent.output_type is session_ops._SessionClassification:
                return agent.output_type(
                    named_entities="AssistantMD",
                    domain="validation",
                    work_product="checkpoint compaction",
                )
            return agent.output_type(source_summary="")

        original_create_agent = session_ops.create_agent
        original_build_model = session_ops.build_model_instance
        original_generate_response = session_ops.generate_response
        session_ops.create_agent = _create_summary_agent_stub
        session_ops.build_model_instance = _build_model_stub
        session_ops.generate_response = _generate_summary_response_stub
        try:
            extraction = await session_ops._summarize_session(
                vault_name=vault.name,
                session_id=session_id,
                summarization_model="stub-model",
            )
        finally:
            session_ops.create_agent = original_create_agent
            session_ops.build_model_instance = original_build_model
            session_ops.generate_response = original_generate_response

        assert (
            extraction["message_count"] == 5
        ), "Session summarization should count effective history, not raw archival history"
        assert (
            extraction["history_revision"] == 4
        ), "Session summarization should report current history revision"

        await self.stop_system()
        self.teardown_scenario()
