"""Manual compaction honors focus and retains session strategy across settings."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.map_author_transport import map_author
from validation.pytest_scenarios.support.session_map_author_http import configure_author
from validation.pytest_scenarios.support.session_map_journey_inputs import (
    conversation,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_manual_map_compaction_pins_strategy_and_records_governed_tasks(
    app: Application,
) -> None:
    vault = app.vault("ManualSessionMapCompactionVault")
    await app.start()
    from core.runtime.execution_tasks import ExecutionTaskKind
    from core.runtime.state import get_runtime_context

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
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "manual-session-map-compaction"
        seed_session(
            store,
            session,
            vault.name,
            conversation(
                [
                    (
                        "Explore whether redevelopment needs legal review.",
                        "Legal review is an option.",
                    ),
                    ("Compare internal policy review.", "Both routes remain open."),
                    (
                        "The land ownership question remains unresolved.",
                        "I will keep it open.",
                    ),
                    ("Prepare the comparison only.", "I will preserve that direction."),
                ]
            ),
        )
        status = app.request(
            f"/api/chat/sessions/{session}/compaction-status",
            params={"vault_name": vault.name},
        )
        assert status.status_code == 200
        assert (
            status.json()["strategy"],
            status.json()["recommended"],
            status.json()["manual_compaction_available"],
        ) == ("session_map", False, True)
        first = await app.request_async(
            f"/api/chat/sessions/{session}/compact",
            "POST",
            {
                "vault_name": vault.name,
                "focus": "Emphasize that the review routes remain options.",
            },
        )
        assert first.status_code == 200 and first.json()["strategy"] == "session_map"
        for key, value in (
            ("compaction_strategy", "recovery_card"),
            ("compaction_type", "none"),
        ):
            assert (
                app.request(
                    f"/api/system/settings/general/{key}", "PUT", {"value": value}
                ).status_code
                == 200
            )
        store.add_messages(
            session,
            vault.name,
            conversation(
                [
                    (
                        "Add cost as another comparison dimension.",
                        "Cost is now part of the comparison.",
                    ),
                    ("Do not choose a route yet.", "Both routes remain options."),
                ]
            ),
        )
        second = await app.request_async(
            f"/api/chat/sessions/{session}/compact", "POST", {"vault_name": vault.name}
        )
        assert second.status_code == 200 and second.json()["strategy"] == "session_map"
        unavailable = await app.request_async(
            f"/api/chat/sessions/{session}/compact", "POST", {"vault_name": vault.name}
        )
        assert (
            unavailable.status_code,
            unavailable.json()["status"],
            unavailable.json()["reason"],
        ) == (200, "unavailable", "no_evictable_groups")
        assert [payload["user_focus"] for payload in server.payloads] == [
            "Emphasize that the review routes remain options.",
            None,
        ]
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        api_tasks = [task for task in tasks if task.source == "api"]
        assert len(api_tasks) == 3 and all(
            task.result is not None for task in api_tasks
        )
        authors = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        assert len(authors) == 2 and all(
            task.parent_task_id in {item.task_id for item in api_tasks}
            for task in authors
        )
        checkpoints = store.list_context_checkpoints(session, vault.name)
        assert [item.checkpoint_kind for item in checkpoints] == [
            "session_map",
            "session_map",
        ]
        latest = store.get_latest_context_checkpoint(session, vault.name)
        current = app.request(
            f"/api/chat/sessions/{session}/compaction-status",
            params={"vault_name": vault.name},
        ).json()
        assert (
            current["context_checkpoint_kind"],
            current["context_checkpoint_id"],
            current["context_boundary_sequence_index"],
        ) == ("session_map", latest.checkpoint_id, latest.last_message_sequence_index)
