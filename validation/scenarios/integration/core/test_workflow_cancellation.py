"""Real workflow runtime behavior through application entry points."""

from __future__ import annotations

import pytest

from core.identity import LOCAL_USER_AUTHORITY
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context
from validation.scenarios.application import Application
from validation.scenarios.support.workflow_runtime import wait_for_created_path

pytestmark = pytest.mark.scenario


async def test_manual_workflow_cancellation(app: Application) -> None:
    vault = app.vault("WorkflowCancellationVault")
    app.write(vault, "AssistantMD/Authoring/cancellable_probe.md", CANCELLABLE_WORKFLOW)
    await app.start()
    task = await get_runtime_context().workflow_governor.start_workflow(
        global_id=f"{vault.name}/cancellable_probe",
        source=ExecutionTaskSource.API,
        background_tasks=get_runtime_context().background_tasks,
        authority=LOCAL_USER_AUTHORITY,
    )
    task_id = task.task_id
    assert bool(task_id), "Workflow start should return a task id"
    created_path = vault / "notes/cancelled-workflow-write.md"
    await wait_for_created_path(created_path)
    assert created_path.exists(), "Workflow should write a file before cancellation"
    checkpoint = app.events.checkpoint()
    cancel_response = app.request(f"/api/tasks/{task_id}/cancel", method="POST")
    assert cancel_response.status_code == 200, "Workflow task cancel should succeed"
    assert (
        cancel_response.json().get("cancelled") is True
    ), "Workflow task cancel should be effective"
    terminal_task = await app.wait_task(task_id)
    assert (
        terminal_task.get("status") == "cancelled"
    ), "Workflow task should be cancelled"
    assert (
        not created_path.exists()
    ), "Cancelled workflow should rollback files created before cancellation"
    app.events.require(
        "task_rollback_completed",
        after=checkpoint,
        task_id=task_id,
        terminal_status="cancelled",
    )


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
