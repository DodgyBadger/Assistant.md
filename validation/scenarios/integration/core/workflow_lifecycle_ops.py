"""
Integration scenario for workflow_run lifecycle operations.

Validates enable/disable idempotency, target resolution, and scheduler side effects.
"""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.executors.base import run_coroutine_job
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.runtime.state import get_runtime_context
from core.scheduling.jobs import setup_scheduler_jobs
from core.tools.workflow_run import WorkflowRun
from validation.core.base_scenario import BaseScenario


class WorkflowLifecycleOpsScenario(BaseScenario):
    """Validate workflow_run enable/disable lifecycle behavior."""

    async def test_scenario(self):
        vault = self.create_vault("WorkflowLifecycleVault")

        self.create_file(
            vault,
            "AssistantMD/Authoring/daily.md",
            DISABLED_WORKFLOW,
        )
        self.create_file(
            vault,
            "AssistantMD/Authoring/ops/daily.md",
            DISABLED_SUBFOLDER_WORKFLOW,
        )

        checkpoint = self.event_checkpoint()
        await self.start_system()
        startup_events = self.events_since(checkpoint)

        self.assert_event_contains(
            startup_events,
            name="workflow_loaded",
            expected={
                "workflow_id": "WorkflowLifecycleVault/daily",
                "enabled": False,
            },
        )
        self.assert_event_contains(
            startup_events,
            name="workflow_loaded",
            expected={
                "workflow_id": "WorkflowLifecycleVault/ops/daily",
                "enabled": False,
            },
        )

        tool = WorkflowRun.get_tool(str(vault))

        listing = await tool.function(operation="list")
        assert "workflow_name: daily" in listing
        assert "workflow_name: ops/daily" in listing

        # Enable canonical top-level workflow.
        checkpoint = self.event_checkpoint()
        enable_out = await tool.function(
            operation="enable_workflow", workflow_name="daily"
        )
        enable_data = self._parse_kv_response(enable_out)
        self.soft_assert_equal(
            enable_data.get("status"), "enabled_now", "Enable should set enabled_now"
        )
        self.soft_assert_equal(
            enable_data.get("success"), "True", "Enable should succeed"
        )
        events = self.events_since(checkpoint)
        self.assert_event_contains(
            events,
            name="workflow_lifecycle_changed",
            expected={
                "operation": "enable_workflow",
                "workflow_id": "WorkflowLifecycleVault/daily",
                "status": "enabled_now",
            },
        )
        self.assert_event_contains(
            events,
            name="workflow_job_created",
            expected={
                "workflow_id": "WorkflowLifecycleVault/daily",
                "action": "created",
            },
        )

        # Admission policy applies to new, persisted, and replaced workflow jobs.
        scheduler = get_runtime_context().scheduler
        job_id = "WorkflowLifecycleVault__daily"
        job = scheduler.get_job(job_id)
        assert job.misfire_grace_time == 60
        original_next_run = job.next_run_time
        scheduler.modify_job(job_id, misfire_grace_time=1)
        await setup_scheduler_jobs(scheduler)
        job = scheduler.get_job(job_id)
        assert job.misfire_grace_time == 60
        assert job.next_run_time == original_next_run
        scheduler.modify_job(job_id, trigger=CronTrigger(hour=23, minute=59))
        await setup_scheduler_jobs(scheduler)
        assert scheduler.get_job(job_id).misfire_grace_time == 60

        # Exercise APScheduler's real admission check without waiting on a clock
        # or executing the workflow's model calls.
        called = []

        async def probe():
            called.append(True)

        probe_scheduler = AsyncIOScheduler()
        probe_scheduler.start(paused=True)
        try:
            probe_job = probe_scheduler.add_job(
                probe, "date", misfire_grace_time=job.misfire_grace_time
            )
            execution_events = await run_coroutine_job(
                probe_job,
                "default",
                [datetime.now(UTC) - timedelta(seconds=2)],
                "validation.scheduler",
            )
            assert called == [True]
            assert [event.code for event in execution_events] == [EVENT_JOB_EXECUTED]
        finally:
            probe_scheduler.shutdown()

        # Idempotent enable.
        checkpoint = self.event_checkpoint()
        enable_again_out = await tool.function(
            operation="enable_workflow", workflow_name="daily"
        )
        enable_again_data = self._parse_kv_response(enable_again_out)
        self.soft_assert_equal(
            enable_again_data.get("status"),
            "already_enabled",
            "Second enable should be idempotent",
        )
        self.soft_assert_equal(
            enable_again_data.get("success"),
            "True",
            "Second enable should still succeed",
        )
        events = self.events_since(checkpoint)
        self.assert_event_contains(
            events,
            name="workflow_lifecycle_changed",
            expected={
                "operation": "enable_workflow",
                "workflow_id": "WorkflowLifecycleVault/daily",
                "status": "already_enabled",
            },
        )

        # Enable subfolder workflow using AssistantMD/Authoring path form.
        checkpoint = self.event_checkpoint()
        enable_subfolder_out = await tool.function(
            operation="enable_workflow",
            workflow_name="AssistantMD/Authoring/ops/daily.md",
        )
        enable_subfolder_data = self._parse_kv_response(enable_subfolder_out)
        self.soft_assert_equal(
            enable_subfolder_data.get("status"),
            "enabled_now",
            "Subfolder path normalization should enable ops/daily",
        )
        events = self.events_since(checkpoint)
        self.assert_event_contains(
            events,
            name="workflow_lifecycle_changed",
            expected={
                "operation": "enable_workflow",
                "workflow_id": "WorkflowLifecycleVault/ops/daily",
                "status": "enabled_now",
            },
        )

        # Disable canonical workflow.
        checkpoint = self.event_checkpoint()
        disable_out = await tool.function(
            operation="disable_workflow", workflow_name="daily"
        )
        disable_data = self._parse_kv_response(disable_out)
        self.soft_assert_equal(
            disable_data.get("status"),
            "disabled_now",
            "Disable should set disabled_now",
        )
        self.soft_assert_equal(
            disable_data.get("success"), "True", "Disable should succeed"
        )
        events = self.events_since(checkpoint)
        self.assert_event_contains(
            events,
            name="workflow_lifecycle_changed",
            expected={
                "operation": "disable_workflow",
                "workflow_id": "WorkflowLifecycleVault/daily",
                "status": "disabled_now",
            },
        )
        self.assert_event_contains(
            events,
            name="workflow_job_removed",
            expected={
                "job_id": "WorkflowLifecycleVault__daily",
                "workflow_id": "WorkflowLifecycleVault/daily",
            },
        )

        # Idempotent disable.
        checkpoint = self.event_checkpoint()
        disable_again_out = await tool.function(
            operation="disable_workflow", workflow_name="daily"
        )
        disable_again_data = self._parse_kv_response(disable_again_out)
        self.soft_assert_equal(
            disable_again_data.get("status"),
            "already_disabled",
            "Second disable should be idempotent",
        )
        self.soft_assert_equal(
            disable_again_data.get("success"),
            "True",
            "Second disable should still succeed",
        )
        events = self.events_since(checkpoint)
        self.assert_event_contains(
            events,
            name="workflow_lifecycle_changed",
            expected={
                "operation": "disable_workflow",
                "workflow_id": "WorkflowLifecycleVault/daily",
                "status": "already_disabled",
            },
        )

        # Not found.
        not_found_out = await tool.function(
            operation="enable_workflow", workflow_name="missing-workflow"
        )
        not_found_data = self._parse_kv_response(not_found_out)
        self.soft_assert_equal(
            not_found_data.get("status"),
            "not_found",
            "Missing workflow should return not_found",
        )
        self.soft_assert_equal(
            not_found_data.get("success"),
            "False",
            "Missing workflow should not succeed",
        )

        # Security boundary: runtime-root paths are rejected.
        invalid_path_out = await tool.function(
            operation="enable_workflow",
            workflow_name="/app/data/WorkflowLifecycleVault/AssistantMD/Authoring/daily.md",
        )
        self.soft_assert(
            "Runtime filesystem roots are not allowed" in invalid_path_out.return_value,
            "Absolute runtime-root workflow path should be rejected",
        )
        self.soft_assert_equal(
            invalid_path_out.metadata.get("status"),
            "failed",
            "Rejected workflow requests should expose structured failure status",
        )

        activity = self.call_api("/api/system/activity-log?limit=200").json()
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
        await self.stop_system()
        self.teardown_scenario()

    def _parse_kv_response(self, text: str) -> dict:
        parsed = {}
        for raw_line in (text or "").splitlines():
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            parsed[key.strip()] = value.strip()
        return parsed


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
