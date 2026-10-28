"""Post-turn author failure does not fail chat or silently change strategy."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.model_failures import (
    PRIVATE_VALUES,
    rejecting_provider,
)
from validation.scenarios.support.stepped_post_turn_inputs import (
    configure_post_turn,
    seed_post_turn,
)

pytestmark = pytest.mark.scenario


async def test_post_turn_author_failure_preserves_chat(app: Application) -> None:
    vault = app.vault("SteppedMapAuthorFailureVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    app.evidence.register_secret(*PRIVATE_VALUES)
    with rejecting_provider() as (server, requests):
        await app.start()
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_post_turn(app, server)
        runtime = get_runtime_context()
        session = "stepped-map-author-failure"
        seed_post_turn(runtime.chat_store, vault.name, session)
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Complete chat despite author failure.",
                "session_id": session,
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        assert requests
        assert (
            runtime.chat_store.get_latest_context_checkpoint(session, vault.name)
            is None
        )
        assert (
            len(runtime.chat_store.get_history(session, vault.name, mode="raw") or [])
            == 6
        )
        authors = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        compactions = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert len(authors) == 1 and len(compactions) == 1
        assert authors[0].status == "failed" and compactions[0].status == "failed"
        assert (
            authors[0].parent_task_id == compactions[0].task_id
            and compactions[0].parent_task_id == result["task"]["task_id"]
        )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        entries = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("event")
            == "session_map_context_reduction_failed"
            and row["data"].get("task_id") == compactions[0].task_id
        ]
        assert (
            len(entries) == 1
            and entries[0]["reason"] == "map_authoring_failed"
            and entries[0]["status"] == "failed"
        )
        assert all(private not in json.dumps(entries) for private in PRIVATE_VALUES)
