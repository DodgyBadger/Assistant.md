"""Real structured author retries and cancellation retain task ownership."""

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.session_map_author_http import configure_author
from validation.pytest_scenarios.support.session_map_batch15_inputs import (
    author_request,
)
from validation.pytest_scenarios.support.session_map_retry_author import retry_author

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("mode", ["schema_retry", "provenance_retry"])
async def test_session_map_author_retries_invalid_output(
    app: Application, mode: str
) -> None:
    vault = app.vault("MapAuthorRetryVault")
    with retry_author() as server:
        server.mode = mode
        await app.start()
        from core.identity import LOCAL_USER_AUTHORITY
        from core.memory.session_map.service import run_session_map_authoring
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        result = await run_session_map_authoring(
            author_request(vault.name), authority=LOCAL_USER_AUTHORITY
        )
        assert len(server.requests) == 2
        if mode == "schema_retry":
            assert result.draft.entries == () and result.draft.trajectory is None
        else:
            assert (
                result.draft.trajectory is not None
                and result.draft.trajectory.sources[0].start == 10
            )
        task = await get_runtime_context().task_coordinator.get_task(result.task_id)
        assert task is not None and task.status == "completed"
        app.evidence.save_json("session-map-retry-requests.json", server.requests)


async def test_session_map_author_cancellation(app: Application) -> None:
    vault = app.vault("MapAuthorCancelVault")
    with retry_author() as server:
        server.mode = "gate"
        await app.start()
        from core.identity import LOCAL_USER_AUTHORITY
        from core.memory.session_map.service import run_session_map_authoring
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        running = asyncio.create_task(
            run_session_map_authoring(
                author_request(vault.name), authority=LOCAL_USER_AUTHORITY
            )
        )
        try:
            assert await asyncio.to_thread(server.started.wait, 30)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            tasks = await get_runtime_context().task_coordinator.list_tasks(
                kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
            )
            assert len(tasks) == 1 and tasks[0].status == "cancelled"
            activity = app.request("/api/system/activity-log", params={"limit": 1000})
            assert activity.status_code == 200
            events = [
                row["data"]
                for row in activity.json()["entries"]
                if row.get("data", {}).get("event") == "session_map_authoring_cancelled"
                and row["data"].get("task_id") == tasks[0].task_id
            ]
            assert (
                len(events) == 1
                and events[0]["status"] == "cancelled"
                and events[0]["reason"] == "authoring_cancelled"
            )
            assert (
                events[0]["session_id"] == "session-map-task"
                and events[0]["vault_name"] == vault.name
            )
        finally:
            server.release.set()
            if not running.done():
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
