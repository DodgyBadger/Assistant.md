"""Integration scenario for cancelling a manually started workflow task."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import LOCAL_USER_AUTHORITY
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context
from validation.support.base_scenario import BaseScenario


class WorkflowCancellationScenario(BaseScenario):
    """Validate manual workflow cancellation rolls back prior file mutations."""

    async def test_scenario(self):
        vault = self.create_vault("WorkflowCancellationVault")
        self.create_file(
            vault,
            "AssistantMD/Authoring/cancellable_probe.md",
            CANCELLABLE_WORKFLOW,
        )

        await self.start_system()

        task = await get_runtime_context().workflow_governor.start_workflow(
            global_id=f"{vault.name}/cancellable_probe",
            source=ExecutionTaskSource.API,
            background_tasks=get_runtime_context().background_tasks,
            authority=LOCAL_USER_AUTHORITY,
        )
        task_id = task.task_id
        self.soft_assert(bool(task_id), "Workflow start should return a task id")

        created_path = Path(vault) / "notes/cancelled-workflow-write.md"
        for _ in range(50):
            if created_path.exists():
                break
            await asyncio.sleep(0.05)

        self.soft_assert(
            created_path.exists(), "Workflow should write a file before cancellation"
        )

        checkpoint = self.event_checkpoint()
        cancel_response = self.call_api(f"/api/tasks/{task_id}/cancel", method="POST")
        self.soft_assert_equal(
            cancel_response.status_code, 200, "Workflow task cancel should succeed"
        )
        self.soft_assert(
            cancel_response.json().get("cancelled") is True,
            "Workflow task cancel should be effective",
        )

        task = await self._wait_for_execution_task(task_id)
        self.soft_assert_equal(
            task.get("status"), "cancelled", "Workflow task should be cancelled"
        )
        self.soft_assert(
            not created_path.exists(),
            "Cancelled workflow should rollback files created before cancellation",
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


CANCELLABLE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Cancellable workflow probe
---
```python
await file_write(
    operation="write",
    path="notes/cancelled-workflow-write.md",
    content="created before cancellation\\n",
)
while True:
    await file_read(operation="list", path=".")
```
"""
