"""Authored workflow inputs and genuine scheduler events for durable history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED
from apscheduler.executors.base import run_coroutine_job

from validation.scenarios.application import Application

DOMAIN_FAILURE_WORKFLOW = """---
run_type: workflow
enabled: true
schedule: cron: 0 0 1 1 *
---
```python
await finish(status="failed", reason="script used a removed tool")
```
"""

RAISED_FAILURE_WORKFLOW = """---
run_type: workflow
enabled: true
schedule: cron: 0 0 1 1 *
---
```python
raise RuntimeError("raised workflow failure")
```
"""

TEMPLATE_INPUT = """---
run_type: workflow
enabled: false
---
```python
await finish(status="completed")
```
"""


async def scheduler_failure_and_miss(app: Application, vault_name: str):
    """Run real jobs at a valid and an expired admission time, replay one miss."""
    from core.runtime.state import get_runtime_context
    from core.scheduling.job_history import record_scheduler_job_event

    scheduler = get_runtime_context().scheduler
    assert scheduler is not None
    failed_job = scheduler.get_job(f"{vault_name}__raised_failure")
    assert failed_job is not None
    failures = await run_coroutine_job(
        failed_job, "default", [datetime.now(UTC)], "validation.scheduler"
    )
    assert len(failures) == 1 and failures[0].code == EVENT_JOB_ERROR
    assert failures[0].exception is not None
    record_scheduler_job_event(failures[0], scheduler=scheduler)
    missed_job = scheduler.get_job(f"{vault_name}__nightly_cleanup")
    assert missed_job is not None
    misses = await run_coroutine_job(
        missed_job,
        "default",
        [datetime.now(UTC) - timedelta(days=1)],
        "validation.scheduler",
    )
    assert len(misses) == 1 and misses[0].code == EVENT_JOB_MISSED
    # A repeated delivery of the same real scheduler event must be idempotent.
    record_scheduler_job_event(misses[0], scheduler=scheduler)
    record_scheduler_job_event(misses[0], scheduler=scheduler)
    app.evidence.save_json(
        "scheduler-event-inputs.json",
        {
            "failure_code": failures[0].code,
            "error_type": type(failures[0].exception).__name__,
            "miss_code": misses[0].code,
            "miss_scheduled_at": misses[0].scheduled_run_time.isoformat(),
        },
    )


def seed_nonempty_output_path(store, vault_name: str):
    """Test optional output-path persistence, not production workflow generation."""
    row = store.create_run(
        workflow_id=f"{vault_name}/output_path_input",
        workflow_name="output_path_input",
        vault_name=vault_name,
        source="api",
        owner_principal_id="local-user",
    )
    return store.finalize_run(
        row.run_id,
        status="failed",
        reason="explicit output-path persistence input",
        output_files=["output.md"],
    )
