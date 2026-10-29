"""Repeated real author rejection preserves pinned context and distinct warnings."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_repair_policy_contracts import (
    configure_map_policy,
    disable_chat_tools,
    repair_author,
    seed_legacy_pending_map,
    settings,
)

pytestmark = pytest.mark.scenario


async def test_pinned_map_auto_failures_remain_distinct_and_chat_usable(
    app: Application,
):
    vault = app.vault("DistinctMapFailures")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    from core.runtime.state import get_runtime_context

    with repair_author() as server:
        configure_author(app, server)
        configure_map_policy(app)
        disable_chat_tools(app)
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "distinct-map-failures"
        seed_legacy_pending_map(store, vault.name, session)
        settings(app, compaction_strategy="recovery_card")
        prior = store.list_context_checkpoints(session, vault.name)
        raw = store.get_stored_messages(session, vault.name, mode="raw")
        server.invalid = True
        turns = []
        for index in range(2):
            turn = await chat_turn(
                app,
                {
                    "vault_name": vault.name,
                    "session_id": session,
                    "prompt": f"Continue safely after rejected reduction {index}.",
                    "model": "test",
                    "context_template": "full_history.md",
                },
            )
            assert turn["terminal_event"]["event"] == "done"
            assert turn["task"]["status"] == "completed"
            turns.append(turn)
            assert store.list_context_checkpoints(session, vault.name) == prior
        after = store.get_stored_messages(session, vault.name, mode="raw")
        assert after[: len(raw)] == raw and len(after) == len(raw) + 4
        assert server.requests
        compactions = await runtime.task_coordinator.list_tasks(
            kind="history_compaction"
        )
        authors = await runtime.task_coordinator.list_tasks(
            kind="session_map_authoring"
        )
        assert len(compactions) == len(authors) == 2
        for turn in turns:
            parent = next(
                task
                for task in compactions
                if task.parent_task_id == turn["task"]["task_id"]
            )
            child = next(
                task for task in authors if task.parent_task_id == parent.task_id
            )
            assert parent.status == child.status == "failed"
            assert child.terminal_error_type == "SessionMapProvenanceError"
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        retained = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("event")
            == "session_map_context_reduction_failed"
            and row.get("data", {}).get("session_id") == session
        ]
        assert len(retained) == 2
        assert {item["task_id"] for item in retained} == {
            task.task_id for task in compactions
        }
        assert len({item["issue"] for item in retained}) == 2
        assert all(
            item.checkpoint_kind == "session_map"
            for item in store.list_context_checkpoints(session, vault.name)
        )
        app.evidence.save_json("distinct-auto-map-failure-warnings.json", retained)
