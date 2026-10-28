"""Manual compaction and legacy upgrades accept valid native tool exchanges."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.map_author_citable import map_author
from validation.pytest_scenarios.support.session_map_author_http import configure_author
from validation.pytest_scenarios.support.session_map_journey_inputs import (
    add_recovery_checkpoint,
    conversation,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_valid_tool_exchanges_compact_and_upgrade_through_real_author(
    app: Application,
) -> None:
    vault = app.vault("ValidToolHistoryMapVault")
    await app.start()
    from core.identity import LOCAL_USER_PRINCIPAL_ID
    from core.runtime.state import get_runtime_context
    from validation.support.tool_history_fixtures import tool_reply_cases

    with map_author() as server:
        configure_author(app, server)
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "50"),
            ("compaction_retained_turns", "1"),
            ("compaction_high_watermark_tokens", "100000"),
            ("compaction_type", "suggested"),
        ):
            assert (
                app.request(
                    f"/api/system/settings/general/{key}", "PUT", {"value": value}
                ).status_code
                == 200
            )
        store = get_runtime_context().chat_store
        cases = [case for case in tool_reply_cases() if not case.issue_codes]
        assert cases
        for case in cases:
            manual = f"manual-valid-{case.name}"
            store.ensure_session(
                manual, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
            )
            store.add_messages(manual, vault.name, case.messages)
            before = store.get_history(manual, vault.name, mode="raw")
            before_requests = len(server.requests)
            response = await app.request_async(
                f"/api/chat/sessions/{manual}/compact",
                "POST",
                {"vault_name": vault.name},
            )
            assert (
                response.status_code == 200
                and response.json()["strategy"] == "session_map"
            ), case.name
            assert len(server.requests) > before_requests
            assert store.get_history(manual, vault.name, mode="raw") == before
            legacy = f"upgrade-valid-{case.name}"
            initial = conversation(
                [
                    (
                        f"Question {i} " + "substantive planning evidence " * 12,
                        f"Answer {i} " + "substantive planning evidence " * 12,
                    )
                    for i in range(6)
                ]
            )
            seed_session(store, legacy, vault.name, initial)
            add_recovery_checkpoint(store, legacy, vault.name, f"{legacy}-recovery", 5)
            store.add_messages(legacy, vault.name, case.messages)
            before = store.get_history(legacy, vault.name, mode="raw")
            before_requests = len(server.requests)
            response = await app.request_async(
                f"/api/chat/sessions/{legacy}/upgrade-context-strategy",
                "POST",
                {"vault_name": vault.name},
            )
            assert response.status_code == 202, case.name
            task = await app.wait_task(response.json()["task"]["task_id"])
            assert task["status"] == "completed", case.name
            assert len(server.requests) > before_requests
            assert store.get_history(legacy, vault.name, mode="raw") == before
