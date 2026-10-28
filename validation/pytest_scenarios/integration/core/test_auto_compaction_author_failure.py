"""An actual author provider failure leaves chat intact and safe diagnostics."""

import json

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.pytest_scenarios.support.chat_tasks import chat_turn
from validation.pytest_scenarios.support.compaction_journeys import (
    add_turn,
    configure_compaction,
)
from validation.pytest_scenarios.support.model_failures import (
    PRIVATE_VALUES,
    rejecting_provider,
)

pytestmark = pytest.mark.scenario


async def test_automatic_compaction_author_provider_failure(app: Application) -> None:
    vault = app.vault("AutoCompactionAuthorFailureVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    app.evidence.register_secret(*PRIVATE_VALUES)
    with rejecting_provider() as (server, requests):
        await app.start()
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_compaction(app, server, mode="auto")
        metadata = app.request("/api/metadata")
        assert metadata.status_code == 200
        disabled = app.request(
            "/api/system/settings/general/disabled_tools",
            method="PUT",
            data={
                "value": json.dumps([row["name"] for row in metadata.json()["tools"]])
            },
        )
        assert disabled.status_code == 200
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "auto-compaction-author-failure"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        add_turn(
            store, vault.name, session, "Earlier user turn.", "Earlier assistant turn."
        )
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Exercise automatic compaction author failure.",
                "session_id": session,
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        assert requests
        assert store.get_latest_context_checkpoint(session, vault.name) is None
        assert store.get_message_count(session, vault.name, mode="raw") == 4
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert len(tasks) == 1
        failed = tasks[0]
        assert (
            failed.status == "failed"
            and failed.parent_task_id == result["task"]["task_id"]
        )
        assert failed.terminal_error_type == "ModelHTTPError"
        assert all(
            private not in str(failed.terminal_reason) for private in PRIVATE_VALUES
        )
        task = app.request(f"/api/tasks/{failed.task_id}")
        assert task.status_code == 200 and all(
            private not in task.text for private in PRIVATE_VALUES
        )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        correlated = [
            row
            for row in activity.json()["entries"]
            if row.get("data", {}).get("task_id")
            in {failed.task_id, result["task"]["task_id"]}
        ]
        assert all(private not in json.dumps(correlated) for private in PRIVATE_VALUES)
        assert (
            sum(
                row.get("data", {}).get("event") == "chat_compaction_failed"
                for row in correlated
            )
            == 1
        )
        assert not any(
            row.get("data", {}).get("event") == "chat_post_turn_compaction_failed"
            for row in correlated
        )
