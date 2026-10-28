"""Real workflow runtime behavior through application entry points."""

from __future__ import annotations

import pytest
from apscheduler.triggers.cron import CronTrigger

from core.runtime.state import get_runtime_context
from core.scheduling.jobs import setup_scheduler_jobs
from validation.scenarios.application import Application
from validation.scenarios.support.workflow_runtime import (
    execute_late_scheduled_workflow,
    workflow_failure,
    workflow_text,
)

pytestmark = pytest.mark.scenario


async def test_workflow_enable_disable_and_scheduler_admission(
    app: Application,
) -> None:
    from core.tools.workflow_run import WorkflowRun

    vault = app.vault("WorkflowLifecycleVault")
    app.write(vault, "AssistantMD/Authoring/daily.md", DISABLED_WORKFLOW)
    app.write(vault, "AssistantMD/Authoring/ops/daily.md", DISABLED_SUBFOLDER_WORKFLOW)
    checkpoint = app.events.checkpoint()
    await app.start()
    app.events.require(
        "workflow_loaded",
        after=checkpoint,
        workflow_id="WorkflowLifecycleVault/daily",
        enabled=False,
    )
    app.events.require(
        "workflow_loaded",
        after=checkpoint,
        workflow_id="WorkflowLifecycleVault/ops/daily",
        enabled=False,
    )
    tool = WorkflowRun.get_tool(str(vault))
    listing = await workflow_text(tool, operation="list")
    assert "workflow_name: daily" in listing
    assert "workflow_name: ops/daily" in listing
    checkpoint = app.events.checkpoint()
    enable_out = await workflow_text(
        tool, operation="enable_workflow", workflow_name="daily"
    )
    enable_data = app.parse_result(enable_out)
    assert enable_data.get("status") == "enabled_now", "Enable should set enabled_now"
    assert enable_data.get("success") == "True", "Enable should succeed"
    app.events.require(
        "workflow_lifecycle_changed",
        after=checkpoint,
        operation="enable_workflow",
        workflow_id="WorkflowLifecycleVault/daily",
        status="enabled_now",
    )
    app.events.require(
        "workflow_job_created",
        after=checkpoint,
        workflow_id="WorkflowLifecycleVault/daily",
        action="created",
    )
    # Scheduler synchronization repairs admission grace without shifting next run.
    scheduler = get_runtime_context().scheduler
    assert scheduler is not None
    job_id = "WorkflowLifecycleVault__daily"
    job = scheduler.get_job(job_id)
    assert job is not None
    assert job.misfire_grace_time == 60
    original_next_run = job.next_run_time
    scheduler.modify_job(job_id, misfire_grace_time=1)
    await setup_scheduler_jobs(scheduler)
    job = scheduler.get_job(job_id)
    assert job is not None
    assert job.misfire_grace_time == 60
    assert job.next_run_time == original_next_run
    scheduler.modify_job(job_id, trigger=CronTrigger(hour=23, minute=59))
    await setup_scheduler_jobs(scheduler)
    assert scheduler.get_job(job_id).misfire_grace_time == 60
    # Execute the actual app job two seconds late; its real workflow must finish.
    await execute_late_scheduled_workflow(
        app, scheduler.get_job(job_id), global_id="WorkflowLifecycleVault/daily"
    )
    # Enable and disable remain idempotent across canonical and subfolder names.
    checkpoint = app.events.checkpoint()
    enable_again_out = await workflow_text(
        tool, operation="enable_workflow", workflow_name="daily"
    )
    enable_again_data = app.parse_result(enable_again_out)
    assert (
        enable_again_data.get("status") == "already_enabled"
    ), "Second enable should be idempotent"
    assert (
        enable_again_data.get("success") == "True"
    ), "Second enable should still succeed"
    app.events.require(
        "workflow_lifecycle_changed",
        after=checkpoint,
        operation="enable_workflow",
        workflow_id="WorkflowLifecycleVault/daily",
        status="already_enabled",
    )
    checkpoint = app.events.checkpoint()
    enable_subfolder_out = await workflow_text(
        tool,
        operation="enable_workflow",
        workflow_name="AssistantMD/Authoring/ops/daily.md",
    )
    enable_subfolder_data = app.parse_result(enable_subfolder_out)
    assert (
        enable_subfolder_data.get("status") == "enabled_now"
    ), "Subfolder path normalization should enable ops/daily"
    app.events.require(
        "workflow_lifecycle_changed",
        after=checkpoint,
        operation="enable_workflow",
        workflow_id="WorkflowLifecycleVault/ops/daily",
        status="enabled_now",
    )
    checkpoint = app.events.checkpoint()
    disable_out = await workflow_text(
        tool, operation="disable_workflow", workflow_name="daily"
    )
    disable_data = app.parse_result(disable_out)
    assert (
        disable_data.get("status") == "disabled_now"
    ), "Disable should set disabled_now"
    assert disable_data.get("success") == "True", "Disable should succeed"
    app.events.require(
        "workflow_lifecycle_changed",
        after=checkpoint,
        operation="disable_workflow",
        workflow_id="WorkflowLifecycleVault/daily",
        status="disabled_now",
    )
    app.events.require(
        "workflow_job_removed",
        after=checkpoint,
        job_id="WorkflowLifecycleVault__daily",
        workflow_id="WorkflowLifecycleVault/daily",
    )
    checkpoint = app.events.checkpoint()
    disable_again_out = await workflow_text(
        tool, operation="disable_workflow", workflow_name="daily"
    )
    disable_again_data = app.parse_result(disable_again_out)
    assert (
        disable_again_data.get("status") == "already_disabled"
    ), "Second disable should be idempotent"
    assert (
        disable_again_data.get("success") == "True"
    ), "Second disable should still succeed"
    app.events.require(
        "workflow_lifecycle_changed",
        after=checkpoint,
        operation="disable_workflow",
        workflow_id="WorkflowLifecycleVault/daily",
        status="already_disabled",
    )
    not_found_out = await workflow_text(
        tool, operation="enable_workflow", workflow_name="missing-workflow"
    )
    not_found_data = app.parse_result(not_found_out)
    assert (
        not_found_data.get("status") == "not_found"
    ), "Missing workflow should return not_found"
    assert (
        not_found_data.get("success") == "False"
    ), "Missing workflow should not succeed"
    invalid_path_out = await workflow_failure(
        tool,
        operation="enable_workflow",
        workflow_name="/app/data/WorkflowLifecycleVault/AssistantMD/Authoring/daily.md",
    )
    assert isinstance(invalid_path_out.return_value, str)
    assert (
        "Runtime filesystem roots are not allowed" in invalid_path_out.return_value
    ), "Absolute runtime-root workflow path should be rejected"
    assert (
        invalid_path_out.metadata.get("status") == "failed"
    ), "Rejected workflow requests should expose structured failure status"
    activity = app.request("/api/system/activity-log?limit=200").json()
    job_events = [
        entry["data"]
        for entry in activity["entries"]
        if entry.get("data", {}).get("event")
        in {
            "workflow_job_created",
            "workflow_job_replaced",
            "workflow_job_removed",
            "workflow_scheduler_sync_completed",
        }
    ]
    assert {row["event"] for row in job_events} == {
        "workflow_job_created",
        "workflow_job_replaced",
        "workflow_job_removed",
        "workflow_scheduler_sync_completed",
    }
    assert all(row["status"] == "completed" for row in job_events)


DISABLED_WORKFLOW = """---
schedule: cron: 0 9 * * *
run_type: workflow
enabled: false
description: Daily lifecycle test
---

## Run

```python
await finish(status="completed", reason="lifecycle-test")
```
"""


DISABLED_SUBFOLDER_WORKFLOW = """---
schedule: cron: 0 10 * * *
run_type: workflow
enabled: false
description: Subfolder daily lifecycle test
---

## Run

```python
await finish(status="completed", reason="lifecycle-test")
```
"""
