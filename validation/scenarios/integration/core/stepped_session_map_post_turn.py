"""Validate opt-in stepped maps through the real post-turn reduction hook."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel  # noqa: E402

from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.checkpoints import (  # noqa: E402
    load_session_map_checkpoint,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SessionMapTrajectory,
    SourceRange,
)
from core.runtime.execution_tasks import ExecutionTaskKind  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SteppedSessionMapPostTurnScenario(BaseScenario):
    """Pin context strategy and defer map failures without crossing formats."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SteppedSessionMapPostTurnVault")
        await self.start_system()

        import core.chat.compaction as compaction
        import core.chat.executor as chat_executor
        import core.memory.session_map.service as session_map_service

        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_author_model", "test"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "1"),
            ("compaction_retained_turns", "1"),
            ("compaction_high_watermark_tokens", "2"),
            ("compaction_type", "auto"),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200, f"{key} setting should update"

        runtime = get_runtime_context()
        store = runtime.chat_store
        successful_session = "stepped-map-post-turn-success"
        failed_session = "stepped-map-post-turn-fallback"
        commit_failed_session = "stepped-map-post-turn-commit-failure"
        for session_id in (successful_session, failed_session, commit_failed_session):
            store.ensure_session(
                session_id,
                vault.name,
                owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
            )
            store.add_messages(
                session_id,
                vault.name,
                [
                    _user("Earlier question about the redevelopment proposal."),
                    _assistant("Earlier analysis of the proposal."),
                    _user("The City owns the underlying land."),
                    _assistant("That fact should be checked with counsel."),
                ],
            )

        def prepare_test_agent(
            vault_name, vault_path, tools, model, thinking=None, chat_mode=None
        ):
            del vault_name, vault_path, tools, model, thinking, chat_mode
            return ("Answer briefly.", "", TestModel(), [])

        author_calls = 0
        private_marker = "PRIVATE_POST_TURN_COMPACTION_FAILURE_CONTENT"

        async def authored_map(**kwargs: object) -> SessionMapDraft:
            nonlocal author_calls
            author_calls += 1
            if author_calls in {2, 3}:
                raise RuntimeError(private_marker + "x" * 8000)
            payload = json.loads(str(kwargs["prompt"]))
            sources = tuple(
                SourceRange(**item["source_range"])
                for item in payload["new_evidence_envelopes"]
            )
            return SessionMapDraft(
                trajectory=SessionMapTrajectory(
                    text="The redevelopment work moved into active legal review.",
                    sources=sources,
                ),
                entries=(
                    SessionMapEntry(
                        id="redevelopment_legal_review",
                        kind="goal",
                        state="active",
                        basis="mixed",
                        text="Continue legal review of the redevelopment proposal.",
                        sources=sources,
                    ),
                ),
            )

        async def recovery_summary(**_kwargs: object) -> str:
            return "Continue reviewing the redevelopment proposal with counsel."

        original_prepare = chat_executor._prepare_agent_config
        original_author = session_map_service._invoke_session_map_model
        original_summary = compaction._generate_compaction_summary
        chat_executor._prepare_agent_config = prepare_test_agent
        session_map_service._invoke_session_map_model = authored_map
        compaction._generate_compaction_summary = recovery_summary
        try:
            successful_chat = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Draft the next legal-review question.",
                    "session_id": successful_session,
                    "tools": [],
                    "model": "test",
                }
            )
            failed_chat = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Prepare another question for counsel.",
                    "session_id": failed_session,
                    "tools": [],
                    "model": "test",
                }
            )
            response = self.call_api(
                "/api/system/settings/general/compaction_strategy",
                method="PUT",
                data={"value": "recovery_card"},
            )
            pinned_chat = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Record the latest legal-review position.",
                    "session_id": successful_session,
                    "tools": [],
                    "model": "test",
                }
            )
            response = self.call_api(
                "/api/system/settings/general/compaction_strategy",
                method="PUT",
                data={"value": "session_map"},
            )
            assert response.status_code == 200
            original_commit = compaction.commit_session_map_context_checkpoint

            def reject_checkpoint(**_kwargs: object) -> None:
                raise ValueError(private_marker + "x" * 8000)

            compaction.commit_session_map_context_checkpoint = reject_checkpoint
            try:
                commit_failed_chat = await self.run_chat_task(
                    {
                        "vault_name": vault.name,
                        "prompt": "Complete the author but reject its checkpoint.",
                        "session_id": commit_failed_session,
                        "tools": [],
                        "model": "test",
                    }
                )
            finally:
                compaction.commit_session_map_context_checkpoint = original_commit
        finally:
            chat_executor._prepare_agent_config = original_prepare
            session_map_service._invoke_session_map_model = original_author
            compaction._generate_compaction_summary = original_summary

        self.soft_assert_equal(
            successful_chat["terminal_event"].get("event"),
            "done",
            "A successful map reduction should not delay chat completion",
        )
        success_checkpoint = store.get_latest_context_checkpoint(
            successful_session, vault.name
        )
        self.soft_assert_equal(
            success_checkpoint.checkpoint_kind if success_checkpoint else None,
            "session_map",
            "Successful post-turn authoring should commit a map checkpoint",
        )
        assert success_checkpoint is not None
        persisted_map = load_session_map_checkpoint(success_checkpoint)
        self.soft_assert_equal(
            persisted_map.entries[0].id,
            "redevelopment_legal_review",
            "The post-turn hook should persist the validated authored map",
        )
        success_raw = (
            store.get_history(successful_session, vault.name, mode="raw") or []
        )
        success_effective = store.get_history(successful_session, vault.name) or []
        self.soft_assert_equal(
            len(success_raw),
            8,
            "Repeated map reductions should retain every canonical chat message",
        )
        self.soft_assert(
            len(success_effective) < len(success_raw),
            "The map path should reduce effective history",
        )

        self.soft_assert_equal(
            failed_chat["terminal_event"].get("event"),
            "done",
            "A failed map author should not fail the completed chat turn",
        )
        failed_checkpoint = store.get_latest_context_checkpoint(
            failed_session, vault.name
        )
        self.soft_assert_equal(
            failed_checkpoint.checkpoint_kind if failed_checkpoint else None,
            None,
            "A failed first map author should not silently select recovery-card compaction",
        )
        failed_raw = store.get_history(failed_session, vault.name, mode="raw") or []
        self.soft_assert_equal(
            len(failed_raw),
            6,
            "Deferred map authoring should preserve every canonical chat message",
        )

        self.soft_assert_equal(
            response.status_code,
            200,
            "The global context-reduction default should update",
        )
        self.soft_assert_equal(
            pinned_chat["terminal_event"].get("event"),
            "done",
            "A pinned map session should continue after the global default changes",
        )
        pinned_checkpoint = store.get_latest_context_checkpoint(
            successful_session, vault.name
        )
        self.soft_assert_equal(
            pinned_checkpoint.checkpoint_kind if pinned_checkpoint else None,
            "session_map",
            "An established map checkpoint should pin the session to Compaction v2",
        )
        self.soft_assert_equal(
            len(
                store.list_context_checkpoints(
                    successful_session,
                    vault.name,
                    checkpoint_kind="session_map",
                )
            ),
            1,
            "A failed pinned-map rewrite should leave the prior checkpoint untouched",
        )

        manual_recovery_rejected = False
        try:
            await compaction.compact_chat_history(
                session_id=successful_session,
                vault_name=vault.name,
                vault_path=str(vault),
            )
        except ValueError:
            manual_recovery_rejected = True
        self.soft_assert(
            manual_recovery_rejected,
            "Direct recovery-card compaction should reject a pinned map session",
        )

        map_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        self.soft_assert_equal(
            [task.status for task in map_tasks],
            ["completed", "failed", "failed", "completed"],
            "Every attempted map author should have an observable task outcome",
        )
        compaction_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        self.soft_assert_equal(
            [task.status for task in compaction_tasks],
            ["completed", "failed", "failed", "failed"],
            "Compaction tasks must cover checkpoint completion as well as authoring",
        )
        chat_ids = {
            successful_chat["task_id"],
            failed_chat["task_id"],
            pinned_chat["task_id"],
            commit_failed_chat["task_id"],
        }
        compaction_ids = {task.task_id for task in compaction_tasks}
        self.soft_assert(
            all(
                task.source == "system" and task.parent_task_id in chat_ids
                for task in compaction_tasks
            ),
            "Automatic V2 compaction should be owned by the active chat task",
        )
        self.soft_assert(
            all(task.parent_task_id in compaction_ids for task in map_tasks),
            "Map authors must be nested under the owning compaction task",
        )
        self.soft_assert_equal(
            (compaction_tasks[0].result or {}).get("checkpoint_id"),
            success_checkpoint.checkpoint_id,
            "The completed task result should identify the durable checkpoint",
        )
        self.soft_assert_equal(
            compaction_tasks[-1].terminal_error_type,
            "ValueError",
            "A failed checkpoint commit must fail the compaction task",
        )
        self.soft_assert_equal(
            store.get_latest_context_checkpoint(commit_failed_session, vault.name),
            None,
            "A failed checkpoint commit must not claim durable compaction",
        )
        self.soft_assert_equal(
            commit_failed_chat["terminal_event"].get("event"),
            "done",
            "Post-turn checkpoint failure must preserve the completed chat",
        )
        all_tasks = await runtime.task_coordinator.list_tasks()
        self.soft_assert(
            all(task.kind != "session_map_classification" for task in all_tasks),
            "Stepped reduction should never dispatch the retired map classifier",
        )

        activity_response = self.call_api("/api/system/activity-log?limit=200")
        assert activity_response.status_code == 200
        entries = [
            entry["data"]
            for entry in activity_response.json()["entries"]
            if entry.get("data", {}).get("event")
            in {
                "compaction_strategy_selected",
                "session_map_context_reduction_started",
                "session_map_context_reduction_plan_selected",
                "session_map_context_reduction_completed",
                "session_map_context_reduction_failed",
            }
        ]
        failures = [
            entry
            for entry in entries
            if entry["event"] == "session_map_context_reduction_failed"
        ]
        self.soft_assert_equal(
            {entry.get("session_id") for entry in failures},
            {successful_session, failed_session, commit_failed_session},
            "All distinct-session reduction failures must remain visible in System Activity",
        )
        self.soft_assert_equal(
            len({entry.get("issue") for entry in failures}),
            3,
            "Failed operations require distinct warning identities",
        )
        self.soft_assert(
            all(
                entry.get("status") == "failed"
                and entry.get("task_id") in compaction_ids
                and len(str(entry.get("error", ""))) <= 200
                for entry in failures
            ),
            "Reduction failure must identify its owning task with bounded diagnostics",
        )
        completed = [
            entry
            for entry in entries
            if entry["event"] == "session_map_context_reduction_completed"
        ]
        self.soft_assert(
            len(completed) == 1
            and completed[0].get("status") == "completed"
            and completed[0].get("task_id") == compaction_tasks[0].task_id,
            "Durable reduction completion must identify the outer compaction task",
        )
        success_lifecycle = {
            entry["event"]: entry
            for entry in entries
            if entry.get("task_id") == compaction_tasks[0].task_id
        }
        self.soft_assert_equal(
            {event: entry.get("status") for event, entry in success_lifecycle.items()},
            {
                "session_map_context_reduction_started": "started",
                "session_map_context_reduction_plan_selected": "selected",
                "session_map_context_reduction_completed": "completed",
            },
            "Map Activity must expose one correlated start/plan/durable-completion lifecycle",
        )
        self.soft_assert(
            all(
                entry.get("parent_task_id") == successful_chat["task_id"]
                for entry in success_lifecycle.values()
            ),
            "Map lifecycle entries must identify the chat task that owns the outer compaction",
        )
        self.soft_assert(
            private_marker not in json.dumps(entries),
            "Compaction-owned Activity must not leak model or checkpoint exception content",
        )

        equality_session = "stepped-map-high-watermark-equality"
        store.ensure_session(
            equality_session, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        equality_history = [
            _user("Older question"),
            _assistant("Older answer"),
            _user("Newest question"),
            _assistant("Newest answer"),
        ]
        store.add_messages(equality_session, vault.name, equality_history)
        threshold = compaction.estimate_history_tokens(equality_history)
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_high_watermark_tokens", str(threshold)),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200
        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=authored_map,
        ):
            status = await compaction.get_compaction_status(
                session_id=equality_session, vault_name=vault.name, store=store
            )
            result = await compaction.maybe_auto_compact_after_turn(
                session_id=equality_session,
                vault_name=vault.name,
                vault_path=str(vault),
            )
        self.soft_assert(
            status.recommended and result is not None,
            "Automatic V2 reduction must run at the exact advertised high watermark",
        )
        checkpoint = store.get_latest_context_checkpoint(equality_session, vault.name)
        self.soft_assert(
            checkpoint is not None and checkpoint.checkpoint_kind == "session_map",
            "High-watermark equality must commit the V2 checkpoint",
        )
        self.assert_no_failures()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
