"""Real streaming tasks publish output and persist history independently of subscribers."""

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_stream_provider import streaming_provider
from validation.pytest_scenarios.support.chat_task_journeys import (
    start_chat,
    wait_for_status,
)
from validation.pytest_scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_background_stream_completion_and_cancellation(app: Application) -> None:
    vault = app.vault("ChatStreamBackgroundTaskVault")
    with streaming_provider() as server:
        await app.start()
        configure_provider(app, server)
        server.release.set()
        task_id = await start_chat(app, vault.name, "background", "Run in background.")
        assert (await app.wait_task(task_id))["status"] == "completed"
        replay = await app.request_async(f"/api/chat/tasks/{task_id}/events")
        assert (
            replay.status_code == 200
            and '"event": "delta"' in replay.text
            and '"event": "done"' in replay.text
        )
        assert "first delta" in replay.text
        detail = app.request(
            "/api/chat/sessions/background", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        messages = detail.json()["messages"]
        assert [row["role"] for row in messages] == ["user", "assistant"]
        assert "first delta final" in messages[-1]["content"]
        server.started.clear()
        server.release.clear()
        cancelled_id = await start_chat(
            app, vault.name, "background-cancel", "Cancel background stream."
        )
        assert await asyncio.to_thread(server.started.wait, 30)
        await wait_for_status(app, cancelled_id, "running")
        response = await app.request_async(
            f"/api/tasks/{cancelled_id}/cancel", method="POST"
        )
        assert response.status_code == 200 and response.json()["cancelled"] is True
        assert (await app.wait_task(cancelled_id))["status"] == "cancelled"
        replay = await app.request_async(f"/api/chat/tasks/{cancelled_id}/events")
        assert replay.status_code == 200 and '"event": "cancelled"' in replay.text
