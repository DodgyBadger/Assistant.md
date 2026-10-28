"""Replay boundaries with actual queued chat and workflow tasks."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_stream_provider import streaming_provider
from validation.scenarios.support.chat_task_journeys import (
    event_payloads,
    start_chat,
    wait_for_status,
)
from validation.scenarios.support.session_ops_chat import configure_provider
from validation.scenarios.support.workflow_runtime import WAITING_WORKFLOW

pytestmark = pytest.mark.scenario


async def test_chat_snapshot_cursor_handoff_and_task_boundaries(
    app: Application,
) -> None:
    vault = app.vault("ChatApiBoundaryVault")
    app.write(vault, "AssistantMD/Authoring/boundary_wait.md", WAITING_WORKFLOW)
    with streaming_provider() as server:
        await app.start()
        from core.identity import LOCAL_USER_AUTHORITY
        from core.runtime.execution_tasks import ExecutionTaskSource
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        runtime = get_runtime_context()
        runtime.chat_store.ensure_session(
            "boundary", vault.name, owner_principal_id="local-user"
        )
        workflow = await runtime.workflow_governor.start_workflow(
            global_id=f"{vault.name}/boundary_wait",
            source=ExecutionTaskSource.API,
            background_tasks=runtime.background_tasks,
            authority=LOCAL_USER_AUTHORITY,
        )
        rejected = await app.request_async(
            f"/api/chat/tasks/{workflow.task_id}/replay-snapshot"
        )
        assert rejected.status_code == 404
        first = await start_chat(app, vault.name, "boundary", "first boundary prompt")
        assert await asyncio.to_thread(server.started.wait, 30)
        await wait_for_status(app, first, "running")
        second = await start_chat(app, vault.name, "boundary", "queued boundary prompt")
        await wait_for_status(app, second, "queued")
        empty = await app.request_async(f"/api/chat/tasks/{second}/replay-snapshot")
        assert empty.status_code == 200
        assert (empty.json()["latest_sequence"], empty.json()["events"]) == (0, [])
        assert empty.json()["terminal"] is False
        async with asyncio.timeout(30):
            while True:
                running = await app.request_async(
                    f"/api/chat/tasks/{first}/replay-snapshot"
                )
                assert running.status_code == 200
                snapshot = running.json()
                if any("first delta" in str(event) for event in snapshot["events"]):
                    break
                await asyncio.sleep(0.05)
        assert snapshot["latest_sequence"] > 0
        server.release.set()
        assert (await app.wait_task(first))["status"] == "completed"
        handoff = await app.request_async(
            f"/api/chat/tasks/{first}/events",
            params={"after_sequence": snapshot["latest_sequence"]},
        )
        assert handoff.status_code == 200
        rows = event_payloads(handoff.text)
        assert rows and rows[-1]["event"] == "done"
        assert all(row["sequence"] > snapshot["latest_sequence"] for row in rows)
        assert all("first delta" not in str(row) for row in rows)
        assert (await app.wait_task(second))["status"] == "completed"
        await app.request_async(f"/api/tasks/{workflow.task_id}/cancel", method="POST")
        await app.wait_task(workflow.task_id)


async def test_running_chat_can_be_cancelled_after_disconnect(app: Application) -> None:
    vault = app.vault("ChatCancellationBoundaryVault")
    with streaming_provider() as server:
        await app.start()
        from core.chat.task_execution import stream_chat_task_sse
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        get_runtime_context().chat_store.ensure_session(
            "boundary", vault.name, owner_principal_id="local-user"
        )
        task_id = await start_chat(app, vault.name, "boundary", "cancel running prompt")
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
        cancelled = await app.request_async(
            f"/api/tasks/{task_id}/cancel", method="POST"
        )
        assert cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
        assert (await app.wait_task(task_id))["status"] == "cancelled"
        replay = await app.request_async(f"/api/chat/tasks/{task_id}/events")
        assert replay.status_code == 200
        assert event_payloads(replay.text)[-1]["event"] == "cancelled"
