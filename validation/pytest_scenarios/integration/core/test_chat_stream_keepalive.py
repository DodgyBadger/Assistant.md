"""Keepalives precede completion, and cancellation terminates subscriptions."""

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_idle_provider import idle_provider
from validation.pytest_scenarios.support.chat_task_journeys import (
    start_chat,
    wait_for_status,
)
from validation.pytest_scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_keepalive_then_completion_and_cancel(app: Application) -> None:
    vault = app.vault("ChatStreamKeepaliveVault")
    with idle_provider() as server:
        await app.start()
        from core.chat.task_execution import stream_chat_task_sse

        configure_provider(app, server)
        task_id = await start_chat(app, vault.name, "idle", "Trigger idle streaming.")
        assert await asyncio.to_thread(server.started.wait, 30)
        await wait_for_status(app, task_id, "running")
        stream = stream_chat_task_sse(task_id=task_id, keepalive_seconds=0.01)
        chunks = []
        try:
            async with asyncio.timeout(30):
                while not any(chunk.startswith(": keepalive") for chunk in chunks):
                    chunks.append(await stream.__anext__())
                assert not any('"event": "delta"' in chunk for chunk in chunks)
                server.release.set()
                async for chunk in stream:
                    chunks.append(chunk)
        finally:
            await stream.aclose()
        keepalive = next(
            i for i, chunk in enumerate(chunks) if chunk.startswith(": keepalive")
        )
        done = next(i for i, chunk in enumerate(chunks) if '"event": "done"' in chunk)
        assert keepalive < done
        assert (await app.wait_task(task_id))["status"] == "completed"
        server.started.clear()
        server.release.clear()
        cancel_id = await start_chat(
            app, vault.name, "idle-cancel", "Cancel idle streaming."
        )
        assert await asyncio.to_thread(server.started.wait, 30)
        await wait_for_status(app, cancel_id, "running")
        stream = stream_chat_task_sse(task_id=cancel_id, keepalive_seconds=0.01)
        response = await app.request_async(
            f"/api/tasks/{cancel_id}/cancel", method="POST"
        )
        assert response.status_code == 200 and response.json()["cancelled"] is True
        try:
            async with asyncio.timeout(30):
                cancel_chunks = [chunk async for chunk in stream]
        finally:
            await stream.aclose()
        assert any('"event": "cancelled"' in chunk for chunk in cancel_chunks)
        assert (await app.wait_task(cancel_id))["status"] == "cancelled"
