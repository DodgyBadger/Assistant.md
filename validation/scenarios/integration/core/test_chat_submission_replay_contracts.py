"""Queued approvals and replay expiry at actual chat API boundaries."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_submission_replay_contracts import (
    start_turn,
    submission_provider,
)
from validation.scenarios.support.chat_task_journeys import wait_for_status

pytestmark = pytest.mark.scenario


async def test_queued_turn_retains_accepted_review_mode(app: Application) -> None:
    vault = app.vault("QueuedApprovalVault")
    await app.start()
    with submission_provider(app) as server:
        session = "queued-approval"
        first = await start_turn(app, vault.name, session, "FIRST_GATE")
        assert await asyncio.to_thread(server.started.wait, 30)
        await wait_for_status(app, first, "running")
        queued = await start_turn(
            app, vault.name, session, "QUEUED_WRITE", mode="inline_edit"
        )
        await wait_for_status(app, queued, "queued")
        cancelled = await start_turn(
            app, vault.name, session, "CANCELLED_INPUT", mode="normal"
        )
        await wait_for_status(app, cancelled, "queued")
        cancel = await app.request_async(
            f"/api/tasks/{cancelled}/cancel", method="POST"
        )
        assert cancel.status_code == 200 and cancel.json()["cancelled"] is True
        assert (await app.wait_task(cancelled))["status"] == "cancelled"
        preference = await app.request_async(
            f"/api/chat/sessions/{session}/mode",
            method="PATCH",
            data={"vault_name": vault.name, "chat_mode": "normal"},
        )
        assert (
            preference.status_code == 200 and preference.json()["chat_mode"] == "normal"
        )
        assert len(server.requests) == 1
        server.release.set()
        assert (await app.wait_task(first))["status"] == "completed"
        assert (await app.wait_task(queued))["status"] == "completed"
        assert not (vault / "queued.md").exists()
        replay = await app.request_async(f"/api/chat/tasks/{queued}/events")
        assert replay.status_code == 200 and '"event": "review_required"' in replay.text
        assert len(server.requests) == 2
        assert all(
            "CANCELLED_INPUT" not in str(row["messages"]) for row in server.requests
        )
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200 and detail.json()["chat_mode"] == "normal"


async def test_expired_raw_cursor_retains_replay_snapshot(app: Application) -> None:
    vault = app.vault("ReplayExpiryVault")
    await app.start()
    with submission_provider(app) as server:
        server.mode = "overflow"
        task_id = await start_turn(app, vault.name, "expiry", "Emit a long stream.")
        assert (await app.wait_task(task_id))["status"] == "completed"
        expired = await app.request_async(
            f"/api/chat/tasks/{task_id}/events", params={"after_sequence": 0}
        )
        assert (
            expired.status_code == 410
            and expired.json()["error"] == "ChatTaskEventCursorExpired"
        )
        assert expired.json()["details"]["oldest_available_sequence"] > 1
        snapshot = await app.request_async(f"/api/chat/tasks/{task_id}/replay-snapshot")
        assert snapshot.status_code == 200 and snapshot.json()["available"] is True
        delta = next(
            row for row in snapshot.json()["events"] if row["event"] == "delta"
        )
        assert delta["choices"][0]["delta"]["content"] == "x" * 700
        handoff = await app.request_async(
            f"/api/chat/tasks/{task_id}/events",
            params={"after_sequence": snapshot.json()["latest_sequence"]},
        )
        assert handoff.status_code == 200 and handoff.text == ""
