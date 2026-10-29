"""Real exhausted author validation leaves upgrade inputs intact and diagnoses safely."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.context_upgrade_recovery_contracts import (
    seed_upgrade_input,
    start_upgrade,
)
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_repair_policy_contracts import (
    configure_map_policy,
    repair_author,
)

pytestmark = pytest.mark.scenario


async def test_upgrade_invalid_author_retains_correlated_failure_and_legacy_history(
    app: Application,
):
    vault = app.vault("UpgradeAuthorFailure")
    await app.start()
    from core.runtime.state import get_runtime_context

    with repair_author() as server:
        configure_author(app, server)
        configure_map_policy(app, low=250, high=500)
        server.invalid = True
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "upgrade-invalid-author"
        raw = seed_upgrade_input(store, vault.name, session)
        prior = store.list_context_checkpoints(session, vault.name)
        effective = store.get_history(session, vault.name)
        task_id = await start_upgrade(app, vault.name, session)
        assert (await app.wait_task(task_id))["status"] == "failed"
        assert len(server.requests) > 1
        task = await runtime.task_coordinator.get_task(task_id)
        assert task.terminal_error_type == "SessionMapProvenanceError"
        event = app.events.require(
            "context_strategy_upgrade_failed",
            task_id=task_id,
            session_id=session,
            vault_name=vault.name,
            status="failed",
            reason="upgrade_execution_failed",
            error_type="SessionMapProvenanceError",
        )
        assert event["data"]["issue"] == f"context_strategy_upgrade_failed:{task_id}"
        assert event["data"]["error"] == "upgrade execution failed"
        assert store.get_history(session, vault.name, mode="raw") == raw
        assert store.get_history(session, vault.name) == effective
        assert store.list_context_checkpoints(session, vault.name) == prior
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        retained = [
            item["data"]
            for item in activity.json()["entries"]
            if item.get("data", {}).get("event") == "context_strategy_upgrade_failed"
            and item.get("data", {}).get("task_id") == task_id
        ]
        assert (
            len(retained) == 1 and retained[0]["reason"] == "upgrade_execution_failed"
        )
        app.evidence.save_json(
            "upgrade-author-failure.json",
            {"event": retained[0], "author_requests": len(server.requests)},
        )
