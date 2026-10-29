"""Manual compaction admitted during upgrade preserves the pinned map result."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.context_upgrade_contention import (
    wait_manual_compaction,
)
from validation.scenarios.support.context_upgrade_recovery_contracts import (
    seed_upgrade_input,
    start_upgrade,
    wait_author,
)
from validation.scenarios.support.map_author_barrier import blocking_map_author
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_repair_policy_contracts import (
    configure_map_policy,
    settings,
)

pytestmark = pytest.mark.scenario


async def test_manual_compaction_contends_with_upgrade_without_recovery_downgrade(
    app: Application,
):
    vault = app.vault("UpgradeCompactionContention")
    await app.start()
    from core.runtime.state import get_runtime_context

    with blocking_map_author() as server:
        configure_author(app, server)
        configure_map_policy(app, low=250, high=500)
        settings(app, default_model="test")
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "upgrade-manual-contention"
        raw = seed_upgrade_input(store, vault.name, session)
        legacy = store.list_context_checkpoints(
            session, vault.name, checkpoint_kind="recovery_card"
        )
        upgrade_id = await start_upgrade(app, vault.name, session)
        await wait_author(server)
        manual = asyncio.create_task(
            app.request_async(
                f"/api/chat/sessions/{session}/compact",
                "POST",
                {"vault_name": vault.name},
            )
        )
        try:
            admitted = await wait_manual_compaction(runtime, session)
            assert not manual.done() and not admitted.is_terminal
            assert store.list_context_checkpoints(session, vault.name) == legacy
            server.release.set()
            assert (await app.wait_task(upgrade_id))["status"] == "completed"
            async with asyncio.timeout(15):
                compacted = await manual
            assert compacted.status_code == 200, compacted.text
            assert store.get_history(session, vault.name, mode="raw") == raw
            assert (
                store.list_context_checkpoints(
                    session, vault.name, checkpoint_kind="recovery_card"
                )
                == legacy
            )
            assert (
                store.get_latest_context_checkpoint(session, vault.name).checkpoint_kind
                == "session_map"
            )
            terminal = await runtime.task_coordinator.get_task(admitted.task_id)
            assert terminal.status == "completed"
            app.evidence.save_json(
                "upgrade-manual-contention.json",
                {
                    "upgrade_task_id": upgrade_id,
                    "manual_task_id": admitted.task_id,
                    "manual_response": compacted.json(),
                    "author_inputs": server.payloads,
                },
            )
        finally:
            server.release.set()
            if not manual.done():
                manual.cancel()
            await asyncio.gather(manual, return_exceptions=True)
