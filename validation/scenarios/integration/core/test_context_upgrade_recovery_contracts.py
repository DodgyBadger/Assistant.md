"""Upgrade stale-result rejection and restart retry preserve canonical data."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.compaction_journeys import add_turn
from validation.scenarios.support.context_upgrade_recovery_contracts import (
    seed_upgrade_input,
    start_upgrade,
    wait_author,
)
from validation.scenarios.support.map_author_barrier import blocking_map_author
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_repair_policy_contracts import (
    configure_map_policy,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("mutation", ["append", "delete"])
async def test_upgrade_rejects_author_result_after_canonical_mutation(
    app: Application, mutation
):
    vault = app.vault("StaleUpgradeResults")
    await app.start()
    from core.chat.chat_store import ChatStore
    from core.runtime.state import get_runtime_context

    with blocking_map_author() as server:
        configure_author(app, server)
        configure_map_policy(app, low=250, high=500)
        store = get_runtime_context().chat_store
        session = "upgrade-stale-result"
        before = seed_upgrade_input(store, vault.name, session)
        checkpoints = store.list_context_checkpoints(session, vault.name)
        task_id = await start_upgrade(app, vault.name, session)
        await wait_author(server)
        if mutation == "append":
            # A second real connection represents an independent canonical
            # writer that cannot share the process-local history gate.
            external = ChatStore(str(app.root / "system"))
            add_turn(
                external,
                vault.name,
                session,
                "A new canonical question.",
                "A new canonical answer.",
            )
            expected = external.get_history(session, vault.name, mode="raw")
            assert (
                expected[: len(before)] == before and len(expected) == len(before) + 2
            )
        else:
            deleted = await app.request_async(
                f"/api/chat/sessions/{session}",
                "DELETE",
                params={"vault_name": vault.name},
            )
            assert deleted.status_code == 200, deleted.text
        server.release.set()
        terminal = await app.wait_task(task_id)
        assert terminal["status"] == "failed"
        app.events.require(
            "context_strategy_upgrade_failed",
            task_id=task_id,
            reason="session_history_changed",
        )
        if mutation == "append":
            assert store.get_history(session, vault.name, mode="raw") == expected
            assert store.list_context_checkpoints(session, vault.name) == checkpoints
        else:
            assert store.get_session(session, vault.name) is None
            assert store.get_stored_messages(session, vault.name, mode="raw") == []
            assert store.list_context_checkpoints(session, vault.name) == []
        app.evidence.save_json("stale-upgrade-author-inputs.json", server.payloads)


async def test_fork_during_upgrade_keeps_independent_legacy_checkpoint(
    app: Application,
):
    vault = app.vault("UpgradeForkSnapshot")
    await app.start()
    from core.runtime.state import get_runtime_context

    with blocking_map_author() as server:
        configure_author(app, server)
        configure_map_policy(app, low=250, high=500)
        store = get_runtime_context().chat_store
        session = "upgrade-fork-parent"
        raw = seed_upgrade_input(store, vault.name, session)
        task_id = await start_upgrade(app, vault.name, session)
        await wait_author(server)
        forked = await app.request_async(
            f"/api/chat/sessions/{session}/fork",
            "POST",
            {"vault_name": vault.name, "through_sequence_index": 19},
        )
        assert forked.status_code == 200, forked.text
        child = forked.json()["session"]["session_id"]
        before = store.get_latest_context_checkpoint(child, vault.name)
        assert before.checkpoint_kind == "recovery_card"
        assert store.get_history(child, vault.name, mode="raw") == raw
        server.release.set()
        terminal = await app.wait_task(task_id)
        assert terminal["status"] == "completed"
        assert (
            store.get_latest_context_checkpoint(session, vault.name).checkpoint_kind
            == "session_map"
        )
        assert store.get_latest_context_checkpoint(child, vault.name) == before
        assert store.get_history(child, vault.name, mode="raw") == raw
        assert store.get_history(session, vault.name, mode="raw") == raw
        assert not store.list_context_checkpoints(
            child, vault.name, checkpoint_kind="session_map"
        )


async def test_upgrade_shutdown_restart_retries_from_unchanged_canonical_history(
    app: Application,
):
    vault = app.vault("UpgradeRestartRecovery")
    await app.start()
    from core.runtime.state import get_runtime_context

    with blocking_map_author() as server:
        configure_author(app, server)
        configure_map_policy(app, low=250, high=500)
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "upgrade-restart"
        raw = seed_upgrade_input(store, vault.name, session)
        prior = store.list_context_checkpoints(session, vault.name)
        task_id = await start_upgrade(app, vault.name, session)
        await wait_author(server)
        await app.controller.stop_system()
        terminal = await runtime.task_coordinator.get_task(task_id)
        assert terminal is not None and terminal.status == "cancelled"
        server.release.set()
        await app.start()
        restarted = get_runtime_context().chat_store
        assert restarted.get_history(session, vault.name, mode="raw") == raw
        assert restarted.list_context_checkpoints(session, vault.name) == prior
        retried = await start_upgrade(app, vault.name, session)
        assert retried != task_id
        result = await app.wait_task(retried)
        assert result["status"] == "completed"
        assert restarted.get_history(session, vault.name, mode="raw") == raw
        assert (
            restarted.get_latest_context_checkpoint(session, vault.name).checkpoint_kind
            == "session_map"
        )
        assert (
            restarted.list_context_checkpoints(
                session, vault.name, checkpoint_kind="recovery_card"
            )
            == prior
        )
        app.evidence.save_json("upgrade-restart-author-inputs.json", server.payloads)
