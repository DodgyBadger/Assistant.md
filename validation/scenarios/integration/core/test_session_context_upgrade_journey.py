"""Legacy recovery checkpoints upgrade to a real authored session map."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.map_author_transport import map_author
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_journey_inputs import (
    add_recovery_checkpoint,
    conversation,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_recovery_card_history_upgrades_to_session_map(app: Application) -> None:
    vault = app.vault("SessionContextStrategyUpgradeVault")
    await app.start()
    from core.memory.session_map.checkpoints import load_session_map_checkpoint
    from core.runtime.execution_tasks import ExecutionTaskKind
    from core.runtime.state import get_runtime_context

    with map_author() as server:
        configure_author(app, server)
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("default_model", "test"),
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
        store = get_runtime_context().chat_store
        unpinned = "strategy-upgrade-unpinned"
        seed_session(
            store,
            unpinned,
            vault.name,
            conversation([("Short question", "Short answer")]),
        )
        session = "strategy-upgrade-v1"
        raw = conversation(
            [
                (
                    f"User contribution {index} "
                    + "substantive planning evidence " * 12,
                    f"Assistant response {index} "
                    + "substantive planning evidence " * 12,
                )
                for index in range(10)
            ]
        )
        seed_session(store, session, vault.name, raw[:6])
        add_recovery_checkpoint(store, session, vault.name, "recovery-one", 5)
        store.add_messages(session, vault.name, raw[6:12])
        add_recovery_checkpoint(store, session, vault.name, "recovery-two", 11)
        store.add_messages(session, vault.name, raw[12:])
        assert store.get_history(session, vault.name, mode="raw") == raw
        listing = app.request("/api/chat/sessions", params={"vault_name": vault.name})
        assert listing.status_code == 200
        sessions = {item["session_id"]: item for item in listing.json()}
        assert (
            sessions[unpinned]["context_strategy"],
            sessions[unpinned]["can_upgrade_to_v2"],
        ) == ("unassigned", False)
        assert (
            sessions[session]["context_strategy"],
            sessions[session]["can_upgrade_to_v2"],
        ) == ("recovery_card", True)
        checkpoint = app.events.checkpoint()
        response = await app.request_async(
            f"/api/chat/sessions/{session}/upgrade-context-strategy",
            "POST",
            {"vault_name": vault.name},
        )
        assert response.status_code == 202
        task_id = response.json()["task"]["task_id"]
        assert (
            app.request(f"/api/chat/sessions/{session}/active-task").status_code == 404
        )
        task = await app.wait_task(task_id)
        assert task["status"] == "completed"
        assert len(server.payloads) >= 2
        latest = store.get_latest_context_checkpoint(session, vault.name)
        assert latest is not None and latest.checkpoint_kind == "session_map"
        assert store.get_history(session, vault.name, mode="raw") == raw
        assert (
            len(
                store.list_context_checkpoints(
                    session, vault.name, checkpoint_kind="recovery_card"
                )
            )
            == 2
        )
        assert (
            len(
                store.list_context_checkpoints(
                    session, vault.name, checkpoint_kind="session_map"
                )
            )
            == 1
        )
        assert load_session_map_checkpoint(latest).trajectory is not None
        assert (
            app.request(
                f"/api/chat/sessions/{session}/map", params={"vault_name": vault.name}
            ).status_code
            == 200
        )
        listing = app.request(
            "/api/chat/sessions", params={"vault_name": vault.name}
        ).json()
        upgraded = next(item for item in listing if item["session_id"] == session)
        assert (
            upgraded["context_strategy"],
            upgraded["can_upgrade_to_v2"],
            upgraded["has_session_map"],
        ) == ("session_map", False, True)
        assert (
            await app.request_async(
                f"/api/chat/sessions/{session}/upgrade-context-strategy",
                "POST",
                {"vault_name": vault.name},
            )
        ).status_code == 409
        for event in (
            "context_strategy_upgrade_started",
            "context_strategy_upgrade_planned",
            "context_strategy_upgrade_completed",
        ):
            app.events.require(after=checkpoint, name=event)
        authors = await get_runtime_context().task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        assert len(authors) == len(server.payloads) and all(
            author.parent_task_id == task_id for author in authors
        )
