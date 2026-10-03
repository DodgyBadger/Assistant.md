"""Integration scenario for automatic post-turn chat history compaction."""

import json
import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class AutoChatHistoryCompactionScenario(BaseScenario):
    """Validate settings-driven automatic compaction after a completed chat turn."""

    async def test_scenario(self):
        vault = self.create_vault("AutoChatHistoryCompactionVault")

        await self.start_system()

        from pydantic_ai.messages import (
            ModelRequest,
            ModelResponse,
            TextPart,
            UserPromptPart,
        )
        from pydantic_ai.models.test import TestModel

        import core.chat.compaction as compaction
        import core.chat.executor as chat_executor
        from core.chat.chat_store import ChatStore
        from core.constants import CHAT_HISTORY_RECOVERY_CARD_PREAMBLE
        from core.identity import LOCAL_USER_AUTHORITY, LOCAL_USER_PRINCIPAL_ID
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import RuntimeStateError, get_runtime_context

        settings_response = self.call_api("/api/system/settings/general")
        assert settings_response.status_code == 200, "General settings should load"
        settings_by_key = {item["key"]: item for item in settings_response.json()}
        assert (
            settings_by_key["compaction_type"]["value"] == "auto"
        ), "New settings files should default chat history compaction to auto"
        compaction_description = settings_by_key["compaction_type"]["description"]
        assert (
            "increase compaction_high_watermark_tokens first" in compaction_description
        ), "Compaction setting copy should steer users to threshold tuning before disabling auto"

        for key, value in (
            ("compaction_type", "auto"),
            ("compaction_retained_turns", "1"),
            ("compaction_high_watermark_tokens", "1"),
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
        runtime = get_runtime_context()
        store = ChatStore(system_root=str(runtime.config.system_root))
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                ModelRequest(parts=[UserPromptPart(content="Earlier user turn.")]),
                ModelResponse(parts=[TextPart(content="Earlier assistant turn.")]),
            ],
        )
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
            "older_count": 2,
            "recent_count": 2,
            "focus": None,
        }, "Automatic compaction should summarize older turns and preserve the newest complete turn"

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
            last_compaction["compaction_high_watermark_tokens"] == 1
        ), "Auto compaction should record the effective high watermark"
        assert (
            last_compaction["compaction_retained_turns"] == 1
        ), "Auto compaction should record effective turn retention"

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
            len(effective_messages) == 3
        ), "Effective history should be compacted to summary plus the recent turn"
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
            store.get_message_count(session_id, vault.name, mode="raw") == 4
        ), "Automatic compaction should preserve raw archival messages"
        history_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert len(history_tasks) == 1
        history_task = history_tasks[0]
        assert history_task.source == "system"
        assert (
            history_task.parent_task_id == chat_result["task_id"]
        ), "Automatic compaction must remain a child of its active chat task"
        assert history_task.status == "completed"
        assert history_task.result is not None
        assert history_task.result["strategy"] == "recovery_card"
        assert history_task.result["checkpoint_id"] == checkpoint.checkpoint_id

        sentinel = "PRIVATE-AUTO-COMPACTION-SENTINEL"

        async def failing_summary(**_kwargs):
            raise RuntimeError(sentinel)

        for failure_phase in ("authoring", "commit"):
            failed_session = f"auto-compaction-failure-{failure_phase}"
            store.ensure_session(
                failed_session,
                vault.name,
                owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
            )
            store.add_messages(
                failed_session,
                vault.name,
                [
                    ModelRequest(parts=[UserPromptPart(content="Earlier user turn.")]),
                    ModelResponse(parts=[TextPart(content="Earlier assistant turn.")]),
                ],
            )
            with (
                patch.object(
                    chat_executor,
                    "_prepare_agent_config",
                    _patched_prepare_agent_config,
                ),
                patch.object(
                    compaction,
                    "_generate_compaction_summary",
                    failing_summary if failure_phase == "authoring" else _summary_stub,
                ),
                ExitStack() as commit_patch,
            ):
                if failure_phase == "commit":
                    commit_patch.enter_context(
                        patch.object(
                            runtime.chat_store,
                            "add_compaction_checkpoint",
                            side_effect=RuntimeError(sentinel),
                        )
                    )
                failed_chat = await self.run_chat_task(
                    {
                        "vault_name": vault.name,
                        "prompt": "Exercise automatic compaction failure.",
                        "session_id": failed_session,
                        "tools": [],
                        "model": "test",
                    }
                )
            assert failed_chat["terminal_event"]["event"] == "done"
            assert (
                store.get_latest_context_checkpoint(failed_session, vault.name) is None
            )
            assert store.get_message_count(failed_session, vault.name, mode="raw") == 4
            tasks = await runtime.task_coordinator.list_tasks(
                kind=ExecutionTaskKind.HISTORY_COMPACTION.value
            )
            failed_task = tasks[-1]
            assert failed_task.status == "failed"
            assert failed_task.parent_task_id == failed_chat["task_id"]
            assert failed_task.terminal_error_type == "RuntimeError"
            assert sentinel not in str(failed_task.terminal_reason)
            task_response = self.call_api(f"/api/tasks/{failed_task.task_id}")
            assert task_response.status_code == 200
            assert sentinel not in task_response.text
            response = self.call_api("/api/system/activity-log?limit=200")
            assert response.status_code == 200
            correlated = [
                entry
                for entry in response.json()["entries"]
                if entry.get("data", {}).get("task_id")
                in {failed_task.task_id, failed_chat["task_id"]}
            ]
            assert sentinel not in json.dumps(correlated)
            assert (
                sum(
                    entry.get("data", {}).get("event") == "chat_compaction_failed"
                    for entry in correlated
                )
                == 1
            )
            assert not any(
                entry.get("data", {}).get("event") == "chat_post_turn_compaction_failed"
                for entry in correlated
            ), "The domain failure must not trigger another executor warning"

        with patch.object(
            chat_executor,
            "maybe_auto_compact_after_turn",
            side_effect=RuntimeError(sentinel),
        ):
            async with runtime.task_coordinator.track_current_task(
                kind=ExecutionTaskKind.CHAT,
                scope="chat_session:auto-compaction-pre-domain-failure",
                source="system",
                label="auto-compaction-pre-domain-failure",
                authority=LOCAL_USER_AUTHORITY,
            ) as fallback_task:
                await chat_executor._try_auto_compact_after_turn(
                    session_id="auto-compaction-pre-domain-failure",
                    vault_name=vault.name,
                    vault_path=str(vault),
                )
        response = self.call_api("/api/system/activity-log?limit=200")
        assert response.status_code == 200
        fallback = [
            entry["data"]
            for entry in response.json()["entries"]
            if entry.get("data", {}).get("event") == "chat_post_turn_compaction_failed"
        ]
        assert len(fallback) == 1
        assert fallback[0]["status"] == "failed"
        assert fallback[0]["error_type"] == "RuntimeError"
        assert fallback[0]["issue"]
        assert fallback[0]["task_id"] == fallback_task.task_id
        assert fallback_task.task_id in fallback[0]["issue"]
        assert sentinel not in json.dumps(fallback)

        admission_session = "auto-compaction-admission-rejected"
        store.ensure_session(
            admission_session,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            admission_session,
            vault.name,
            [
                ModelRequest(parts=[UserPromptPart(content="Earlier user turn.")]),
                ModelResponse(parts=[TextPart(content="Earlier assistant turn.")]),
                ModelRequest(parts=[UserPromptPart(content="Current user turn.")]),
                ModelResponse(parts=[TextPart(content="Current assistant turn.")]),
            ],
        )
        async with runtime.task_coordinator.track_current_task(
            kind=ExecutionTaskKind.CHAT,
            scope=f"chat_session:{admission_session}",
            source="system",
            label=admission_session,
            authority=LOCAL_USER_AUTHORITY,
        ) as admission_parent:
            await runtime.task_coordinator.mark_completed(admission_parent.task_id)
            try:
                await compaction.maybe_auto_compact_after_turn(
                    session_id=admission_session,
                    vault_name=vault.name,
                    vault_path=str(vault),
                )
            except RuntimeError:
                pass
            else:
                raise AssertionError("Compaction admission failures must propagate")
            await chat_executor._try_auto_compact_after_turn(
                session_id=admission_session,
                vault_name=vault.name,
                vault_path=str(vault),
            )
        assert not await runtime.task_coordinator.list_child_tasks(
            admission_parent.task_id
        )
        assert (
            store.get_latest_context_checkpoint(admission_session, vault.name) is None
        )
        assert store.get_message_count(admission_session, vault.name, mode="raw") == 4
        response = self.call_api("/api/system/activity-log?limit=200")
        assert response.status_code == 200
        admission_failures = [
            entry["data"]
            for entry in response.json()["entries"]
            if entry.get("data", {}).get("event") == "chat_post_turn_compaction_failed"
            and entry["data"].get("session_id") == admission_session
        ]
        assert len(admission_failures) == 1
        assert admission_failures[0]["status"] == "failed"
        assert admission_failures[0]["error_type"] == "RuntimeError"
        assert admission_failures[0]["task_id"] == admission_parent.task_id
        assert admission_parent.task_id in admission_failures[0]["issue"]

        await self.stop_system()
        try:
            await compaction.maybe_auto_compact_after_turn(
                session_id=session_id,
                vault_name=vault.name,
                vault_path=str(vault),
            )
        except RuntimeStateError:
            pass
        else:
            raise AssertionError(
                "Automatic compaction must require runtime task ownership"
            )
        assert len(store.list_context_checkpoints(session_id, vault.name)) == 1
