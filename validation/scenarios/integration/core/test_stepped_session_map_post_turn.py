"""Post-turn session-map reduction retains canonical history and task lineage."""

import json
import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.session_map_author_http import author_provider
from validation.scenarios.support.stepped_post_turn_inputs import (
    configure_post_turn,
    seed_post_turn,
)

pytestmark = pytest.mark.scenario


async def test_post_turn_map_and_checkpoint_commit_failure(app: Application) -> None:
    vault = app.vault("SteppedSessionMapPostTurnVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    private = "PRIVATE_POST_TURN_COMPACTION_FAILURE_CONTENT"
    app.evidence.register_secret(private)
    with author_provider() as server:
        await app.start()
        from core.memory.session_map.checkpoints import load_session_map_checkpoint
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_post_turn(app, server)
        runtime = get_runtime_context()
        store = runtime.chat_store
        for session in ("stepped-map-success", "stepped-map-commit-failure"):
            seed_post_turn(store, vault.name, session)
        success = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Continue the legal review.",
                "session_id": "stepped-map-success",
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert success["terminal_event"]["event"] == "done"
        checkpoint = store.get_latest_context_checkpoint(
            "stepped-map-success", vault.name
        )
        assert checkpoint is not None and checkpoint.checkpoint_kind == "session_map"
        assert load_session_map_checkpoint(checkpoint).entries
        raw = store.get_history("stepped-map-success", vault.name, mode="raw") or []
        effective = store.get_history("stepped-map-success", vault.name) or []
        assert len(raw) == 6 and len(effective) < len(raw)
        with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
            conn.execute(
                "CREATE TRIGGER reject_stepped_checkpoint BEFORE INSERT ON chat_compaction_checkpoints WHEN NEW.session_id = 'stepped-map-commit-failure' BEGIN SELECT RAISE(ABORT, 'PRIVATE_POST_TURN_COMPACTION_FAILURE_CONTENT'); END"
            )
        failed = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Reject the checkpoint after authoring.",
                "session_id": "stepped-map-commit-failure",
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert failed["terminal_event"]["event"] == "done"
        assert (
            store.get_latest_context_checkpoint(
                "stepped-map-commit-failure", vault.name
            )
            is None
        )
        assert (
            len(
                store.get_history("stepped-map-commit-failure", vault.name, mode="raw")
                or []
            )
            == 6
        )
        authors = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        compactions = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert [task.status for task in authors] == ["completed", "completed"]
        assert [task.status for task in compactions] == ["completed", "failed"]
        assert [task.parent_task_id for task in compactions] == [
            success["task"]["task_id"],
            failed["task"]["task_id"],
        ]
        assert all(task.source == "system" for task in compactions)
        assert [task.parent_task_id for task in authors] == [
            task.task_id for task in compactions
        ]
        assert compactions[0].result["checkpoint_id"] == checkpoint.checkpoint_id
        assert compactions[1].terminal_error_type == "IntegrityError"
        all_tasks = await runtime.task_coordinator.list_tasks()
        assert all(task.kind != "session_map_classification" for task in all_tasks)
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        entries = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("task_id")
            in {task.task_id for task in compactions}
        ]
        success_rows = [
            row for row in entries if row.get("task_id") == compactions[0].task_id
        ]
        assert {
            row["event"]: row["status"]
            for row in success_rows
            if row.get("event")
            in {
                "session_map_context_reduction_started",
                "session_map_context_reduction_plan_selected",
                "session_map_context_reduction_completed",
            }
        } == {
            "session_map_context_reduction_started": "started",
            "session_map_context_reduction_plan_selected": "selected",
            "session_map_context_reduction_completed": "completed",
        }
        failure = [
            row
            for row in entries
            if row.get("event") == "session_map_context_reduction_failed"
            and row.get("task_id") == compactions[1].task_id
        ]
        assert (
            len(failure) == 1
            and failure[0]["reason"] == "checkpoint_commit_failed"
            and failure[0]["status"] == "failed"
        )
        assert len(str(failure[0]["error"])) <= 200 and private not in json.dumps(
            entries
        )
