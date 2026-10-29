"""Real legacy session setup and public upgrade admission/barrier operations."""

from __future__ import annotations

import asyncio

from validation.scenarios.support.session_map_journey_inputs import (
    add_recovery_checkpoint,
    conversation,
    seed_session,
)


def seed_upgrade_input(store, vault, session):
    raw = conversation(
        [
            (
                f"Question {index} " + "substantive planning evidence " * 12,
                f"Answer {index} " + "substantive planning evidence " * 12,
            )
            for index in range(10)
        ]
    )
    seed_session(store, session, vault, raw[:6])
    add_recovery_checkpoint(store, session, vault, f"{session}-legacy", 5)
    store.add_messages(session, vault, raw[6:])
    return raw


async def start_upgrade(app, vault, session):
    response = await app.request_async(
        f"/api/chat/sessions/{session}/upgrade-context-strategy",
        "POST",
        {"vault_name": vault},
    )
    assert response.status_code == 202, response.text
    return response.json()["task"]["task_id"]


async def wait_author(server):
    assert await asyncio.to_thread(server.entered.wait, 10)
