"""Real chat stream replay and per-session queue behavior."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_stream_provider import streaming_provider
from validation.scenarios.support.chat_task_journeys import (
    start_chat,
    wait_for_status,
)
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_chat_stream_replay_and_disconnect(app: Application) -> None:
    vault = app.vault("StreamReplayVault")
    with streaming_provider() as server:
        await app.start()
        from core.chat.task_execution import stream_chat_task_sse
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        get_runtime_context().chat_store.ensure_session(
            "stream", vault.name, owner_principal_id="local-user"
        )
        initial = app.request(
            "/api/chat/sessions/stream", params={"vault_name": vault.name}
        ).json()["history_revision"]
        missing = app.request("/api/chat/sessions/stream/active-task")
        assert missing.status_code == 404
        assert missing.json()["details"]["history_revision"] == initial
        task_id = await start_chat(app, vault.name, "stream", "Stream events over API.")
        assert await asyncio.to_thread(server.started.wait, 30)
        await wait_for_status(app, task_id, "running")
        stream = stream_chat_task_sse(task_id=task_id, keepalive_seconds=0.05)
        try:
            async with asyncio.timeout(30):
                while "first delta" not in await stream.__anext__():
                    pass
        finally:
            await stream.aclose()
        assert (await app.request_async(f"/api/tasks/{task_id}")).json()[
            "status"
        ] == "running"
        detached = await app.request_async(f"/api/chat/tasks/{task_id}/replay-snapshot")
        assert detached.status_code == 200
        assert any("first delta" in str(event) for event in detached.json()["events"])
        server.release.set()
        assert (await app.wait_task(task_id))["status"] == "completed"
        missing = await app.request_async("/api/chat/sessions/stream/active-task")
        assert (
            missing.status_code == 404
            and missing.json()["details"]["history_revision"] > initial
        )
        snapshot = (
            await app.request_async(f"/api/chat/tasks/{task_id}/replay-snapshot")
        ).json()
        assert snapshot["available"] is True and snapshot["task_id"] == task_id
        assert snapshot["terminal"] is True and snapshot["latest_sequence"] > 0
        assert snapshot["events"][-1]["event"] == "done"
        fork_index = snapshot["events"][-1]["fork_sequence_index"]
        assert fork_index == 1
        fork = await app.request_async(
            "/api/chat/sessions/stream/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": fork_index},
        )
        assert fork.status_code == 200
        replay = await app.request_async(f"/api/chat/tasks/{task_id}/events")
        assert (
            replay.status_code == 200
            and "first delta" in replay.text
            and '"event": "done"' in replay.text
        )


async def test_chat_session_queue_and_queued_cancellation(app: Application) -> None:
    vault = app.vault("ChatQueueVault")
    with streaming_provider() as server:
        await app.start()
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        get_runtime_context().chat_store.ensure_session(
            "queue", vault.name, owner_principal_id="local-user"
        )
        first = await start_chat(app, vault.name, "queue", "first prompt")
        assert await asyncio.to_thread(server.started.wait, 30)
        await wait_for_status(app, first, "running")
        second = await start_chat(app, vault.name, "queue", "second prompt")
        await wait_for_status(app, second, "queued")
        assert len(server.requests) == 1
        active = await app.request_async("/api/chat/sessions/queue/active-task")
        assert active.status_code == 200 and active.json()["task_id"] == first
        cancelled = await start_chat(app, vault.name, "queue", "cancel prompt")
        await wait_for_status(app, cancelled, "queued")
        await get_runtime_context().task_coordinator.cancel_task(
            cancelled, reason="validation_cancel_queued"
        )
        assert (await app.wait_task(cancelled))["status"] == "cancelled"
        events = await app.request_async(f"/api/chat/tasks/{cancelled}/events")
        assert '"event": "cancelled"' in events.text
        assert len(server.requests) == 1
        server.release.set()
        assert (await app.wait_task(first))["status"] == "completed"
        assert (await app.wait_task(second))["status"] == "completed"
        assert len(server.requests) == 2
        assert "first prompt" in str(server.requests[1]["messages"])
        assert "first delta final" in str(server.requests[1]["messages"])
        replay = await app.request_async(f"/api/chat/tasks/{second}/events")
        assert "first delta" in replay.text and '"event": "done"' in replay.text
