"""Integration scenario for workflow_run asynchronous task operations."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.runtime.state import get_runtime_context
from core.tools.workflow_run import WorkflowRun
from validation.support.base_scenario import BaseScenario


class WorkflowRunAsyncScenario(BaseScenario):
    """Validate workflow_run start/status/cancel operations use workflow task lifecycle."""

    async def test_scenario(self):
        vault = self.create_vault("WorkflowRunAsyncVault")
        self.create_file(
            vault,
            "AssistantMD/Authoring/async_probe.md",
            ASYNC_PROBE_WORKFLOW,
        )

        await self.start_system()

        tool = WorkflowRun.get_tool(str(vault))
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            start_out = await tool.function(
                operation="start", workflow_name="async_probe"
            )
        start_data = self._parse_kv_response(start_out)
        task_id = start_data.get("task_id", "")
        self.soft_assert_equal(
            start_data.get("success"), "True", "workflow_run start should succeed"
        )
        self.soft_assert(bool(task_id), "workflow_run start should return a task id")

        created_path = Path(vault) / "notes/workflow-run-async-write.md"
        for _ in range(50):
            if created_path.exists():
                break
            await asyncio.sleep(0.05)

        self.soft_assert(
            created_path.exists(),
            "Started workflow should write a file before cancellation",
        )

        status_out = await tool.function(operation="status", task_id=task_id)
        status_data = self._parse_kv_response(status_out)
        self.soft_assert_equal(
            status_data.get("success"), "True", "workflow_run status should succeed"
        )
        self.soft_assert_equal(
            status_data.get("task_id"),
            task_id,
            "workflow_run status should report the same task",
        )
        self.soft_assert_equal(
            status_data.get("heartbeat_status"),
            "workflow_running",
            "workflow_run status should expose the latest workflow heartbeat",
        )
        self.soft_assert(
            status_data.get("heartbeat_age_seconds") != "",
            "workflow_run status should expose heartbeat age",
        )
        self.soft_assert_equal(
            status_data.get("heartbeat_stale"),
            "False",
            "freshly running workflow should not be marked stale",
        )
        self.assert_event_contains(
            name="execution_task_heartbeat",
            expected={
                "task_id": task_id,
                "heartbeat_status": "workflow_running",
            },
        )
        stale_status_out = WorkflowRun._format_task_status(
            replace(
                await get_runtime_context().task_coordinator.get_task(task_id),
                last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=120),
            )
        )
        stale_status_data = self._parse_kv_response(stale_status_out)
        self.soft_assert_equal(
            stale_status_data.get("heartbeat_stale"),
            "True",
            "workflow_run status should mark old heartbeat data stale",
        )

        checkpoint = self.event_checkpoint()
        cancel_out = await tool.function(operation="cancel", task_id=task_id)
        cancel_data = self._parse_kv_response(cancel_out)
        self.soft_assert_equal(
            cancel_data.get("success"),
            "True",
            "workflow_run cancel should be effective",
        )
        self.soft_assert_equal(
            cancel_data.get("task_id"),
            task_id,
            "workflow_run cancel should report the same task",
        )

        task = await self._wait_for_execution_task(task_id)
        self.soft_assert_equal(
            task.get("status"), "cancelled", "Workflow task should be cancelled"
        )
        self.soft_assert(
            not created_path.exists(),
            "Cancelled workflow_run task should rollback files created before cancellation",
        )
        self.assert_event_contains(
            self.events_since(checkpoint),
            name="task_rollback_completed",
            expected={
                "task_id": task_id,
                "terminal_status": "cancelled",
            },
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _parse_kv_response(self, text: str) -> dict:
        parsed = {}
        for raw_line in (text or "").splitlines():
            if ":" not in raw_line:
                continue
            key, value = raw_line.split(":", 1)
            parsed[key.strip()] = value.strip()
        return parsed


ASYNC_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Async workflow_run probe
---
```python
await file_write(
    operation="write",
    path="notes/workflow-run-async-write.md",
    content="created before cancellation\\n",
)
while True:
    await file_read(operation="list", path=".")
```
"""
