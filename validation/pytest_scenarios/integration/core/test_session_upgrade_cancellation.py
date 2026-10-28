"""An active upgrade is deduplicated and cancellation preserves legacy state."""

from __future__ import annotations

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.map_author_barrier import blocking_map_author
from validation.pytest_scenarios.support.session_map_author_http import configure_author
from validation.pytest_scenarios.support.session_map_journey_inputs import (
    add_recovery_checkpoint,
    conversation,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_active_upgrade_is_deduplicated_and_cancellation_preserves_history(
    app: Application,
) -> None:
    vault = app.vault("SessionUpgradeCancellationVault")
    await app.start()
    from core.runtime.execution_tasks import ExecutionTaskKind
    from core.runtime.state import get_runtime_context

    with blocking_map_author() as server:
        configure_author(app, server)
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "250"),
            ("compaction_retained_turns", "2"),
            ("compaction_high_watermark_tokens", "500"),
            ("compaction_type", "auto"),
        ):
            assert (
                app.request(
                    f"/api/system/settings/general/{key}", "PUT", {"value": value}
                ).status_code
                == 200
            )
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "upgrade-cancelled"
        raw = conversation(
            [
                (
                    f"Question {i} " + "substantive planning evidence " * 12,
                    f"Answer {i} " + "substantive planning evidence " * 12,
                )
                for i in range(10)
            ]
        )
        seed_session(store, session, vault.name, raw[:6])
        add_recovery_checkpoint(store, session, vault.name, "old-recovery", 5)
        store.add_messages(session, vault.name, raw[6:])
        before = store.get_history(session, vault.name)
        endpoint = f"/api/chat/sessions/{session}/upgrade-context-strategy"
        started = await app.request_async(endpoint, "POST", {"vault_name": vault.name})
        assert started.status_code == 202
        task_id = started.json()["task"]["task_id"]
        assert await asyncio.to_thread(server.entered.wait, 10)
        duplicate = await app.request_async(
            endpoint, "POST", {"vault_name": vault.name}
        )
        assert (
            duplicate.status_code == 202
            and duplicate.json()["task"]["task_id"] == task_id
        )
        assert (
            app.request(f"/api/chat/sessions/{session}/active-task").status_code == 404
        )
        cancelled = await app.request_async(f"/api/tasks/{task_id}/cancel", "POST")
        assert cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
        task = await app.wait_task(task_id)
        assert task["status"] == "cancelled"
        server.release.set()
        assert store.get_history(session, vault.name, mode="raw") == raw
        assert store.get_history(session, vault.name) == before
        assert (
            store.get_latest_context_checkpoint(session, vault.name).checkpoint_kind
            == "recovery_card"
        )
        children = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        owned_children = [
            child for child in children if child.parent_task_id == task_id
        ]
        assert owned_children and all(
            child.status == "cancelled" for child in owned_children
        )
