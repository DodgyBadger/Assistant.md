"""Job observation, authorization, wait and control use the real coordinator."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_job_tool_observes_and_controls_owned_tasks(app: Application) -> None:
    vault = app.vault("JobToolVault")
    await app.start()
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import (
        LOCAL_USER_AUTHORITY,
        ExecutionAuthority,
        use_execution_authority,
    )
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.tools.job import JOB_LIST_MAX_ITEMS, JOB_MAX_IDS, Job

    coordinator = get_runtime_context().task_coordinator
    tool = Job.get_tool()
    invoke = tool.function
    schema = tool.tool_def.parameters_json_schema
    assert schema["$defs"]["JobOperation"]["enum"] == [
        "list",
        "status",
        "wait",
        "cancel",
    ]
    array = next(
        item
        for item in schema["properties"]["job_ids"]["anyOf"]
        if item.get("type") == "array"
    )
    assert array["maxItems"] == JOB_MAX_IDS
    assert (
        schema["properties"]["timeout_seconds"]["minimum"],
        schema["properties"]["timeout_seconds"]["maximum"],
    ) == (10.0, 3600.0)
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        wrapped = resolve_tool_binding(["job"], vault_path=str(vault)).tool_functions[0]
        assert wrapped.tool_def.parameters_json_schema["$defs"]["JobOperation"][
            "enum"
        ] == ["list", "status", "wait", "cancel"]
        for operation in ("status", "cancel"):
            with pytest.raises(ValueError):
                await invoke(operation=operation)
        for operation in ("status", "wait", "cancel"):
            with pytest.raises(ValueError):
                await invoke(
                    operation=operation,
                    job_ids=[f"task-{i}" for i in range(JOB_MAX_IDS + 1)],
                )

    async def create(label, *, authority=LOCAL_USER_AUTHORITY, **kwargs):
        return await coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope=f"job-tool:{label}",
            source=ExecutionTaskSource.SYSTEM,
            label=label,
            authority=authority,
            **kwargs,
        )

    parent = await create("parent")
    owned = await create(
        "owned",
        parent_task_id=parent.task_id,
        detached_from_parent_lifecycle=True,
        metadata={
            "active_tools": ["file_read"],
            "usage": {"request_count": 2, "tool_call_count": 3},
        },
    )
    inaccessible = await create(
        "inaccessible", authority=ExecutionAuthority(principal_id="another-principal")
    )
    with use_execution_authority(ExecutionAuthority(principal_id="job-tool-principal")):
        for operation in ("status", "cancel"):
            result = json.loads(
                await invoke(operation=operation, job_ids=[inaccessible.task_id])
            )
            assert result["jobs"][0]["outcome"] == "not_found"
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        listed = json.loads(
            await invoke(
                operation="list",
                kind=ExecutionTaskKind.CHAT.value,
                include_terminal=False,
            )
        )
        assert owned.task_id in {item["job_id"] for item in listed["jobs"]}
        assert all("recent_activity" not in item for item in listed["jobs"])
        status = json.loads(await invoke(operation="status", job_ids=[owned.task_id]))[
            "jobs"
        ][0]
        assert (
            status["job_id"],
            status["detached_from_parent_lifecycle"],
            status["active_tools"],
            status["usage"],
        ) == (
            owned.task_id,
            True,
            ["file_read"],
            {"request_count": 2, "tool_call_count": 3},
        )
    snapshot = app.request(f"/api/tasks/{owned.task_id}")
    assert snapshot.status_code == 200
    row = snapshot.json()
    assert (
        row["parent_task_id"] == parent.task_id
        and row["detached_from_parent_lifecycle"] is True
    )
    assert (
        isinstance(row["revision"], int)
        and row["heartbeat_status"] == "queued"
        and "result" not in row
    )

    async def complete():
        await asyncio.sleep(0.02)
        await coordinator.record_result(owned.task_id, {"text": "job complete"})
        await coordinator.mark_completed(owned.task_id)

    completion = asyncio.create_task(complete())
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        waited = json.loads(
            await invoke(
                operation="wait", job_ids=[owned.task_id], timeout_seconds=10.0
            )
        )
        assert (
            waited["timed_out"] is False
            and waited["jobs"][0]["result"]["text"] == "job complete"
        )
        assert (
            json.loads(
                await invoke(
                    operation="wait", job_ids=[owned.task_id], timeout_seconds=10.0
                )
            )["timed_out"]
            is False
        )
    await completion
    assert "result" not in app.request(f"/api/tasks/{owned.task_id}").json()
    first, second = await create("wait-first"), await create("wait-second")

    async def complete_second():
        await asyncio.sleep(0.02)
        await coordinator.mark_completed(second.task_id)

    completion = asyncio.create_task(complete_second())
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        waited = json.loads(
            await invoke(
                operation="wait",
                job_ids=[first.task_id, second.task_id],
                timeout_seconds=10.0,
            )
        )
    await completion
    assert [item["status"] for item in waited["jobs"]] == ["queued", "completed"]
    attention = await create("attention")

    async def update_attention():
        await asyncio.sleep(0.02)
        await coordinator.publish_progress(
            attention.task_id,
            metadata={"attention_reason": "validation"},
            health_status="attention_required",
        )

    update = asyncio.create_task(update_attention())
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        waited = json.loads(
            await invoke(
                operation="wait", job_ids=[attention.task_id], timeout_seconds=10.0
            )
        )
    await update
    assert waited["jobs"][0]["health_status"] == "attention_required"
    with use_execution_authority(ExecutionAuthority(principal_id="job-tool-principal")):
        concealed = json.loads(
            await invoke(
                operation="wait",
                job_ids=[inaccessible.task_id, attention.task_id],
                timeout_seconds=10.0,
            )
        )
    assert [item.get("outcome") for item in concealed["jobs"]] == [
        "not_found",
        "not_found",
    ]
    single = await create("single-cancel")
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        cancelled_one = json.loads(
            await invoke(operation="cancel", job_ids=[single.task_id])
        )
    assert len(cancelled_one["jobs"]) == 1
    assert cancelled_one["jobs"][0]["job_id"] == single.task_id
    assert cancelled_one["jobs"][0]["cancelled"] is True
    assert (await coordinator.get_task(single.task_id)).status == "cancelled"
    one, two = await create("bulk-one"), await create("bulk-two")
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        cancelled = json.loads(
            await invoke(operation="cancel", job_ids=[one.task_id, two.task_id])
        )
    assert [item["cancelled"] for item in cancelled["jobs"]] == [True, True]

    # Use the production list cap rather than replacing it with a test value.
    for index in range(JOB_LIST_MAX_ITEMS + 1):
        await create(f"list-bound-{index}")
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        bounded = json.loads(
            await invoke(
                operation="list",
                kind=ExecutionTaskKind.CHAT.value,
                include_terminal=False,
            )
        )
    assert bounded["truncated"] is True and len(bounded["jobs"]) == JOB_LIST_MAX_ITEMS
    assert all("recent_activity" not in item for item in bounded["jobs"])
    timeout_task = await create("timeout")
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        timer, timed_out = await asyncio.gather(
            invoke(operation="wait", timeout_seconds=10.0),
            invoke(
                operation="wait", job_ids=[timeout_task.task_id], timeout_seconds=10.0
            ),
        )
    timer_result, timeout_result = json.loads(timer), json.loads(timed_out)
    assert timer_result["timer_completed"] is True
    assert (
        timeout_result["timed_out"] is True
        and timeout_result["jobs"][0]["job_id"] == timeout_task.task_id
    )
    delegate = await coordinator.create_queued_task(
        kind=ExecutionTaskKind.DELEGATE,
        scope="delegate_parent:job-tool-session",
        source=ExecutionTaskSource.TOOL,
        label="delegate-only",
        authority=LOCAL_USER_AUTHORITY,
    )
    assert (
        app.request("/api/chat/sessions/job-tool-session/active-task").status_code
        == 404
    )
    await coordinator.cancel_task(delegate.task_id, reason="validation_cleanup")
