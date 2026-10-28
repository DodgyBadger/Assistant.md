"""Real application behavior staged for the pytest migration."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.workflows import (
    ASYNC_PROBE_WORKFLOW,
)

pytestmark = pytest.mark.scenario


async def test_async_cancellation_rollback(app: Application) -> None:
    import asyncio
    from pathlib import Path

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.workflow_run import WorkflowRun

    vault = app.vault("WorkflowRunAsyncVault")
    app.write(vault, "AssistantMD/Authoring/async_probe.md", ASYNC_PROBE_WORKFLOW)
    await app.start()
    tool = WorkflowRun.get_tool(str(vault))
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        start_out = await tool.function(operation="start", workflow_name="async_probe")
    start_data = app.parse_result(start_out)
    task_id = start_data.get("task_id", "")
    assert start_data.get("success") == "True", "workflow_run start should succeed"
    assert bool(task_id), "workflow_run start should return a task id"
    created_path = Path(vault) / "notes/workflow-run-async-write.md"
    for _ in range(50):
        if created_path.exists():
            break
        await asyncio.sleep(0.05)
    assert (
        created_path.exists()
    ), "Started workflow should write a file before cancellation"
    status_out = await tool.function(operation="status", task_id=task_id)
    status_data = app.parse_result(status_out)
    assert status_data.get("success") == "True", "workflow_run status should succeed"
    assert (
        status_data.get("task_id") == task_id
    ), "workflow_run status should report the same task"
    assert (
        status_data.get("heartbeat_status") == "workflow_running"
    ), "workflow_run status should expose the latest workflow heartbeat"
    assert (
        status_data.get("heartbeat_age_seconds") != ""
    ), "workflow_run status should expose heartbeat age"
    assert (
        status_data.get("heartbeat_stale") == "False"
    ), "freshly running workflow should not be marked stale"
    app.events.require(
        "execution_task_heartbeat",
        **{"task_id": task_id, "heartbeat_status": "workflow_running"},
    )
    # Exercise the actual published status with real elapsed time, without
    # fabricating task state or patching the application clock or threshold.
    async with asyncio.timeout(90):
        while True:
            observed_status = app.parse_result(
                await tool.function(operation="status", task_id=task_id)
            )
            assert observed_status.get("status") == "running", observed_status
            if observed_status.get("heartbeat_stale") == "True":
                break
            await asyncio.sleep(2)
    app.evidence.save("stale-heartbeat.json", observed_status)
    assert (
        observed_status.get("heartbeat_stale") == "True"
    ), "A running workflow without a recent heartbeat should be reported stale"
    checkpoint = app.events.checkpoint()
    cancel_out = await tool.function(operation="cancel", task_id=task_id)
    cancel_data = app.parse_result(cancel_out)
    assert (
        cancel_data.get("success") == "True"
    ), "workflow_run cancel should be effective"
    assert (
        cancel_data.get("task_id") == task_id
    ), "workflow_run cancel should report the same task"
    task = await app.wait_task(task_id)
    assert task.get("status") == "cancelled", "Workflow task should be cancelled"
    assert (
        not created_path.exists()
    ), "Cancelled workflow_run task should rollback files created before cancellation"
    app.events.require(
        "task_rollback_completed",
        after=checkpoint,
        **{"task_id": task_id, "terminal_status": "cancelled"},
    )
