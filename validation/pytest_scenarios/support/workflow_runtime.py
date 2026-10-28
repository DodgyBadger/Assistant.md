"""Real workflow inputs, runtime actions, and application observation summaries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from apscheduler.events import EVENT_JOB_EXECUTED
from apscheduler.executors.base import run_coroutine_job
from apscheduler.job import Job
from pydantic_ai import Tool
from pydantic_ai.messages import ToolReturn

from validation.pytest_scenarios.application import Application

WAITING_WORKFLOW = """---
run_type: workflow
enabled: false
description: Real concurrency and timeout probe
---
```python
await job(operation="wait", timeout_seconds=10)
await finish(status="completed", reason="probe-finished")
```
"""


def configure_workflow_limit(app: Application, key: str, value: str) -> None:
    response = app.request(
        f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
    )
    assert response.status_code == 200, response.text


async def execute_workflow(app: Application, global_id: str) -> dict[str, Any]:
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskSource
    from core.runtime.state import get_runtime_context

    result = await get_runtime_context().workflow_governor.execute_workflow(
        global_id=global_id,
        source=ExecutionTaskSource.SCHEDULER,
        authority=LOCAL_USER_AUTHORITY,
    )
    payload = result.to_dict()
    app.evidence.append("workflow-results.jsonl", payload)
    return payload


async def workflow_pair(
    app: Application, global_ids: tuple[str, str]
) -> dict[str, Any]:
    checkpoint = app.events.checkpoint()
    results = await asyncio.gather(
        *(execute_workflow(app, identity) for identity in global_ids)
    )
    timeline: list[str] = []
    active = 0
    maximum = 0
    for row in app.events.select(after=checkpoint):
        name, data = row["name"], row["data"]
        if data.get("workflow_id") not in global_ids:
            continue
        if name == "workflow_task_started":
            active += 1
            maximum = max(maximum, active)
            timeline.append(f"start:{data['workflow_id']}")
        elif name == "workflow_task_completed":
            timeline.append(f"end:{data['workflow_id']}")
            active -= 1
    summary = {
        "statuses": [row["status"] for row in results],
        "max_active": maximum,
        "timeline": timeline,
    }
    app.evidence.save("workflow-concurrency.json", summary)
    return summary


async def execute_late_scheduled_workflow(
    app: Application, job: Job | None, *, global_id: str
) -> None:
    """Admit the app's real job two seconds late and observe its durable result."""
    assert job is not None
    events = await run_coroutine_job(
        job,
        "default",
        [datetime.now(UTC) - timedelta(seconds=2)],
        "validation.scheduler",
    )
    assert [event.code for event in events] == [EVENT_JOB_EXECUTED]
    history = app.request("/api/workflows/runs", params={"global_id": global_id})
    assert history.status_code == 200, history.text
    assert history.json()["runs"][0]["status"] == "completed"


async def wait_for_created_path(path: Path) -> None:
    for _ in range(50):
        if path.exists():
            return
        await asyncio.sleep(0.05)


def install_workflow_timeout(app: Application, *, seconds: float) -> None:
    """Seed the fractional runtime timeout through real persisted configuration."""
    configuration = app.configuration()
    configuration["settings"]["workflow_task_timeout_seconds"]["value"] = seconds
    app.install_configuration(configuration)


async def _invoke_workflow_tool(
    tool: Tool, *, operation: str, workflow_name: str = ""
) -> str | ToolReturn:
    # Pydantic AI erases the callable's defaults and optional context in Tool.function.
    # WorkflowRun.get_tool binds a context-free async function with these defaults.
    invoke = cast(Callable[..., Awaitable[str | ToolReturn]], tool.function)
    return await invoke(operation=operation, workflow_name=workflow_name)


async def workflow_text(tool: Tool, *, operation: str, workflow_name: str = "") -> str:
    result = await _invoke_workflow_tool(
        tool, operation=operation, workflow_name=workflow_name
    )
    assert isinstance(result, str), result
    return result


async def workflow_failure(
    tool: Tool, *, operation: str, workflow_name: str
) -> ToolReturn:
    result = await _invoke_workflow_tool(
        tool, operation=operation, workflow_name=workflow_name
    )
    assert isinstance(result, ToolReturn), result
    return result
