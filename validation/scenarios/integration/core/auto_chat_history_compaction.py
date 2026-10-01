"""Integration scenario for automatic post-turn chat history compaction."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class AutoChatHistoryCompactionScenario(BaseScenario):
    """Validate settings-driven automatic compaction after a completed chat turn."""

    async def test_scenario(self):
        vault = self.create_vault("AutoChatHistoryCompactionVault")

        await self.start_system()

        from pydantic_ai.models.test import TestModel

        import core.chat.compaction as compaction
        import core.chat.executor as chat_executor
        from core.chat.chat_store import ChatStore
        from core.constants import CHAT_HISTORY_RECOVERY_CARD_PREAMBLE
        from core.runtime.state import get_runtime_context

        settings_response = self.call_api("/api/system/settings/general")
        assert settings_response.status_code == 200, "General settings should load"
        settings_by_key = {item["key"]: item for item in settings_response.json()}
        assert (
            settings_by_key["compaction_type"]["value"] == "auto"
        ), "New settings files should default chat history compaction to auto"
        compaction_description = settings_by_key["compaction_type"]["description"]
        assert (
            "increase compaction_token_threshold first" in compaction_description
        ), "Compaction setting copy should steer users to threshold tuning before disabling auto"

        for key, value in (
            ("compaction_type", "auto"),
            ("compaction_keep_recent", "1"),
            ("compaction_token_threshold", "1"),
            ("compaction_author_model", "test"),
            ("compaction_author_thinking", "low"),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200, f"{key} setting should update"

        def _patched_prepare_agent_config(
            vault_name, vault_path, tools, model, thinking=None, chat_mode=None
        ):
            del vault_name, vault_path, tools, model, thinking
            return ("Answer briefly.", "", TestModel(), [])

        captured_summary_inputs = {}

        async def _summary_stub(*, older_messages, recent_messages, focus):
            captured_summary_inputs["older_count"] = len(older_messages)
            captured_summary_inputs["recent_count"] = len(recent_messages)
            captured_summary_inputs["focus"] = focus
            return (
                "Current objective: answer the auto-compaction validation prompt.\n"
                "Next steps: continue from the preserved recent assistant response."
            )

        original_prepare_agent_config = chat_executor._prepare_agent_config
        original_generate_summary = compaction._generate_compaction_summary
        chat_executor._prepare_agent_config = _patched_prepare_agent_config
        compaction._generate_compaction_summary = _summary_stub
        session_id = "auto_chat_history_compaction_session"
        try:
            chat_result = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Trigger automatic compaction after this short answer.",
                    "session_id": session_id,
                    "tools": [],
                    "model": "test",
                },
            )
        finally:
            chat_executor._prepare_agent_config = original_prepare_agent_config
            compaction._generate_compaction_summary = original_generate_summary

        assert (
            chat_result["start_response"].status_code == 200
        ), "Chat task start should succeed"
        assert (
            chat_result["terminal_event"].get("event") == "done"
        ), "Chat task should complete before auto compaction assertion"
        assert captured_summary_inputs == {
            "older_count": 1,
            "recent_count": 1,
            "focus": None,
        }, "Automatic compaction should summarize older history and preserve recent history"

        runtime = get_runtime_context()
        store = ChatStore(system_root=str(runtime.config.system_root))
        metadata = store.get_session_metadata(session_id, vault.name)
        last_compaction = metadata.get("last_compaction")
        assert (
            last_compaction is not None
        ), "Automatic compaction should record session metadata"
        assert (
            last_compaction["source"] == "system"
        ), "Auto compaction should run as a system task"
        assert (
            last_compaction["trigger"] == "auto"
        ), "Auto compaction should record automatic trigger"
        assert (
            last_compaction["reason"] == "token_threshold"
        ), "Auto compaction should record threshold reason"
        assert (
            last_compaction["prompt_contract_version"] == "recovery-card-v5"
        ), "Auto compaction should record prompt contract version"
        assert (
            last_compaction["compaction_type"] == "auto"
        ), "Auto compaction should record effective policy"
        assert (
            last_compaction["author_model"] == "test"
        ), "Recovery-card provenance should record the shared compaction author model"
        assert (
            last_compaction["author_thinking"] == "low"
        ), "Recovery-card provenance should record the shared compaction author thinking"
        assert (
            last_compaction["compaction_token_threshold"] == 1
        ), "Auto compaction should record effective threshold"
        assert (
            last_compaction["compaction_keep_recent"] == 1
        ), "Auto compaction should record effective recent-message setting"

        checkpoint = store.get_latest_compaction_checkpoint(session_id, vault.name)
        assert checkpoint is not None, "Automatic compaction should record a checkpoint"
        checkpoint_metadata = json.loads(checkpoint.metadata_json or "{}")
        assert (
            checkpoint_metadata["trigger"] == "auto"
        ), "Checkpoint should record automatic trigger"
        assert (
            checkpoint_metadata["reason"] == "token_threshold"
        ), "Checkpoint should record threshold reason"

        effective_messages = store.get_stored_messages(session_id, vault.name)
        assert (
            len(effective_messages) == 2
        ), "Effective history should be compacted to summary plus recent message"
        assert (
            effective_messages[0].role == "system"
        ), "Effective history should start with the automatic compaction card"
        assert (
            "AssistantMD compacted chat history" in effective_messages[0].content_text
        ), "Automatic compaction card should use the standard marker"
        assert (
            CHAT_HISTORY_RECOVERY_CARD_PREAMBLE in effective_messages[0].content_text
        ), "Automatic compaction card should include retrieval guidance"
        assert (
            store.get_message_count(session_id, vault.name, mode="raw") == 2
        ), "Automatic compaction should preserve raw archival messages"
