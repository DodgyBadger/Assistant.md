"""Scheduler discovery, persistence, execution and malformed-workflow isolation."""

from __future__ import annotations

from datetime import datetime

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.startup import (
    MALFORMED_SCHEDULE_WORKFLOW,
    QUICK_JOB_2_WORKFLOW,
    QUICK_JOB_UPDATED_WORKFLOW,
    QUICK_JOB_WORKFLOW,
    restart,
    scheduler_record,
    trigger_scheduled_job,
)

pytestmark = pytest.mark.scenario


async def test_startup_scheduler_persistence_and_resilience(app: Application) -> None:
    vault = app.vault("SystemTest")
    app.write(vault, "AssistantMD/Authoring/quick_job.md", QUICK_JOB_WORKFLOW)
    app.write(
        vault, "AssistantMD/Authoring/planning/quick_job_2.md", QUICK_JOB_2_WORKFLOW
    )
    checkpoint = app.events.checkpoint()
    await app.start()
    for workflow_id, job_id in (
        ("SystemTest/quick_job", "SystemTest__quick_job"),
        ("SystemTest/planning/quick_job_2", "SystemTest__planning__quick_job_2"),
    ):
        app.events.require("workflow_loaded", after=checkpoint, workflow_id=workflow_id)
        app.events.require(
            "workflow_job_created", after=checkpoint, job_id=job_id, action="created"
        )
    original = app.events.require(
        "workflow_job_created", after=checkpoint, workflow_id="SystemTest/quick_job"
    )["data"]
    original_subfolder = app.events.require(
        "workflow_job_created",
        after=checkpoint,
        workflow_id="SystemTest/planning/quick_job_2",
    )["data"]

    checkpoint = app.events.checkpoint()
    await restart(app)
    restored = scheduler_record(app, "SystemTest/quick_job", checkpoint)
    restored_subfolder = scheduler_record(
        app, "SystemTest/planning/quick_job_2", checkpoint
    )
    for key in ("next_run_time", "trigger", "job_name"):
        assert restored.get(key) == original.get(key)
    for key in ("next_run_time", "trigger"):
        assert restored_subfolder.get(key) == original_subfolder.get(key)

    app.write(vault, "AssistantMD/Authoring/quick_job.md", QUICK_JOB_UPDATED_WORKFLOW)
    checkpoint = app.events.checkpoint()
    await restart(app)
    updated = app.events.require(
        "workflow_job_replaced",
        after=checkpoint,
        workflow_id="SystemTest/quick_job",
        action="replaced",
    )["data"]
    assert updated.get("trigger") != original.get("trigger")
    assert updated.get("next_run_time") is not None

    checkpoint = app.events.checkpoint()
    await trigger_scheduled_job(app, "SystemTest/quick_job")
    app.events.require("job_executed", after=checkpoint, job_id="SystemTest__quick_job")
    output = vault / f"results/{datetime.now().strftime('%Y-%m-%d')}.md"
    assert output.exists() and output.stat().st_size > 0

    checkpoint = app.events.checkpoint()
    await restart(app)
    await restart(app)
    assert (
        scheduler_record(app, "SystemTest/quick_job", checkpoint).get("next_run_time")
        is not None
    )

    app.write(
        vault,
        "AssistantMD/Authoring/malformed_schedule.md",
        MALFORMED_SCHEDULE_WORKFLOW,
    )
    checkpoint = app.events.checkpoint()
    await restart(app)
    for workflow_id in ("SystemTest/quick_job", "SystemTest/planning/quick_job_2"):
        app.events.require("workflow_loaded", after=checkpoint, workflow_id=workflow_id)
        scheduler_record(app, workflow_id, checkpoint)
    app.events.require(
        "workflow_load_failed",
        after=checkpoint,
        vault="SystemTest",
        workflow_name="malformed_schedule",
        error_type="ValueError",
    )
