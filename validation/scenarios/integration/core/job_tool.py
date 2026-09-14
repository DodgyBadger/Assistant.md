"""Validate generic process-local job observation and control."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import (
    LOCAL_USER_AUTHORITY,
    ExecutionAuthority,
    use_execution_authority,
)
from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
from validation.core.base_scenario import BaseScenario


class JobToolScenario(BaseScenario):
    """Validate the public job tool against synthetic execution tasks."""

    async def test_scenario(self):
        vault = self.create_vault("JobToolVault")
        await self.start_system()
        runtime = self._runtime()

        from core.tools.job import JOB_MAX_IDS, Job

        job_tool = Job.get_tool()
        invoke = cast(Any, job_tool.function)
        tool_schema = job_tool.tool_def.parameters_json_schema
        operation_schema = tool_schema["$defs"]["JobOperation"]
        self.soft_assert_equal(
            operation_schema.get("enum"),
            ["list", "status", "wait", "cancel"],
            "Job operation schema should enumerate the supported operations",
        )
        job_ids_schema = tool_schema["properties"]["job_ids"]
        id_array_schema = next(
            item for item in job_ids_schema["anyOf"] if item.get("type") == "array"
        )
        self.soft_assert_equal(
            id_array_schema.get("maxItems"),
            JOB_MAX_IDS,
            "Job schema should bound batch size",
        )
        timeout_schema = tool_schema["properties"]["timeout_seconds"]
        self.soft_assert_equal(
            (timeout_schema.get("minimum"), timeout_schema.get("maximum")),
            (10.0, 3600.0),
            "Job schema should expose wait bounds",
        )
        from core.authoring.shared.tool_binding import resolve_tool_binding

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            wrapped_job = resolve_tool_binding(
                ["job"],
                vault_path=str(vault),
            ).tool_functions[0]
        wrapped_operation_schema = wrapped_job.tool_def.parameters_json_schema["$defs"][
            "JobOperation"
        ]
        self.soft_assert_equal(
            wrapped_operation_schema.get("enum"),
            ["list", "status", "wait", "cancel"],
            "Wrapped job tools should preserve resolvable enum annotations",
        )
        for operation in ("status", "cancel"):
            try:
                await invoke(operation=operation)
            except ValueError:
                pass
            else:
                self.soft_assert(
                    False,
                    f"Job {operation} should require at least one job ID",
                )
        for operation in ("status", "wait", "cancel"):
            try:
                await invoke(
                    operation=operation,
                    job_ids=[f"task-{index}" for index in range(JOB_MAX_IDS + 1)],
                )
            except ValueError:
                pass
            else:
                self.soft_assert(
                    False, f"Job {operation} should reject oversized batches"
                )

        parent = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:parent",
            source=ExecutionTaskSource.SYSTEM,
            label="parent-job",
            authority=LOCAL_USER_AUTHORITY,
        )
        owned = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:owned",
            source=ExecutionTaskSource.SYSTEM,
            label="owned-job",
            authority=LOCAL_USER_AUTHORITY,
            parent_task_id=parent.task_id,
            metadata={
                "active_tools": ["file_read"],
                "usage": {"request_count": 2, "tool_call_count": 3},
            },
        )
        inaccessible = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:inaccessible",
            source=ExecutionTaskSource.SYSTEM,
            label="inaccessible-job",
            authority=ExecutionAuthority(principal_id="another-principal"),
        )

        with use_execution_authority(
            ExecutionAuthority(principal_id="job-tool-principal")
        ):
            concealed = _decode(
                await invoke(operation="status", job_ids=[inaccessible.task_id])
            )
            concealed_cancel = _decode(
                await invoke(operation="cancel", job_ids=[inaccessible.task_id])
            )
        self.soft_assert_equal(
            concealed["jobs"][0]["outcome"],
            "not_found",
            "Job status should conceal inaccessible task IDs as not found",
        )
        self.soft_assert_equal(
            concealed_cancel["jobs"][0]["outcome"],
            "not_found",
            "Job cancel should conceal inaccessible task IDs as not found",
        )

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            import core.tools.job as job_module

            original_list_maximum = job_module.JOB_LIST_MAX_ITEMS
            job_module.JOB_LIST_MAX_ITEMS = 1
            try:
                listed = _decode(
                    await invoke(
                        operation="list",
                        kind=ExecutionTaskKind.CHAT.value,
                        include_terminal=False,
                    )
                )
            finally:
                job_module.JOB_LIST_MAX_ITEMS = original_list_maximum
            full_list = _decode(
                await invoke(
                    operation="list",
                    kind=ExecutionTaskKind.CHAT.value,
                    include_terminal=False,
                )
            )
            status = _decode(await invoke(operation="status", job_ids=[owned.task_id]))

        listed_ids = {item["job_id"] for item in full_list["jobs"]}
        self.soft_assert_equal(
            owned.task_id in listed_ids,
            True,
            "Job list should expose accessible process-local jobs",
        )
        self.soft_assert_equal(
            (listed["truncated"], len(listed["jobs"])),
            (True, 1),
            "Job list should report and enforce its compact response bound",
        )
        self.soft_assert_equal(
            "recent_activity" in listed["jobs"][0],
            False,
            "Job list summaries should omit detailed activity",
        )
        self.soft_assert_equal(
            status["jobs"][0]["job_id"],
            owned.task_id,
            "Job status should use public job_id naming",
        )
        self.soft_assert_equal(
            status["jobs"][0]["active_tools"],
            ["file_read"],
            "Job status should project bounded live activity",
        )
        self.soft_assert_equal(
            status["jobs"][0]["usage"],
            {"request_count": 2, "tool_call_count": 3},
            "Job status should project bounded aggregate usage",
        )
        api_snapshot_response = self.call_api(f"/api/tasks/{owned.task_id}")
        self.soft_assert_equal(
            api_snapshot_response.status_code,
            200,
            "Execution task API should expose an accessible delegate-style child",
        )
        api_snapshot = api_snapshot_response.json()
        self.soft_assert_equal(
            api_snapshot.get("parent_task_id"),
            parent.task_id,
            "Execution task API should project parent task identity",
        )
        self.soft_assert_equal(
            isinstance(api_snapshot.get("revision"), int),
            True,
            "Execution task API should project revision state",
        )
        self.soft_assert_equal(
            api_snapshot.get("heartbeat_status"),
            "queued",
            "Execution task API should project heartbeat state",
        )
        self.soft_assert_equal(
            "result" in api_snapshot,
            False,
            "Execution task API snapshots should omit process-local result bodies",
        )

        async def _complete_owned() -> None:
            await asyncio.sleep(0.02)
            await runtime.task_coordinator.record_result(
                owned.task_id,
                {"text": "job complete"},
            )
            await runtime.task_coordinator.mark_completed(owned.task_id)

        completion = asyncio.create_task(_complete_owned())
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            waited = _decode(
                await invoke(
                    operation="wait",
                    job_ids=[owned.task_id],
                    timeout_seconds=10.0,
                )
            )
        await completion
        self.soft_assert_equal(
            waited["timed_out"],
            False,
            "Job wait should wake when a selected job becomes terminal",
        )
        self.soft_assert_equal(
            waited["jobs"][0]["result"]["text"],
            "job complete",
            "Job wait should include the bounded terminal result",
        )
        terminal_api_snapshot = self.call_api(f"/api/tasks/{owned.task_id}").json()
        self.soft_assert_equal(
            "result" in terminal_api_snapshot,
            False,
            "Execution task API should omit terminal result bodies",
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            already_terminal = _decode(
                await invoke(
                    operation="wait",
                    job_ids=[owned.task_id],
                    timeout_seconds=10.0,
                )
            )
        self.soft_assert_equal(
            already_terminal["timed_out"],
            False,
            "Job wait should return immediately for an already-terminal job",
        )

        original_minimum = job_module.JOB_WAIT_MIN_SECONDS
        job_module.JOB_WAIT_MIN_SECONDS = 0.001
        try:
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                timer = _decode(await invoke(operation="wait", timeout_seconds=0.002))
                timeout_task = await runtime.task_coordinator.create_queued_task(
                    kind=ExecutionTaskKind.CHAT,
                    scope="job-tool:timeout",
                    source=ExecutionTaskSource.SYSTEM,
                    label="timeout-job",
                    authority=LOCAL_USER_AUTHORITY,
                )
                timed_out = _decode(
                    await invoke(
                        operation="wait",
                        job_ids=[timeout_task.task_id],
                        timeout_seconds=0.002,
                    )
                )
        finally:
            job_module.JOB_WAIT_MIN_SECONDS = original_minimum
        self.soft_assert_equal(
            timer["timer_completed"],
            True,
            "Job wait without IDs should act as a plain timer",
        )
        self.soft_assert_equal(
            (timed_out["timed_out"], timed_out["jobs"][0]["job_id"]),
            (True, timeout_task.task_id),
            "Job timeout should return the latest selected-job snapshot",
        )

        wait_any_first = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:wait-any",
            source=ExecutionTaskSource.SYSTEM,
            label="wait-any-first",
            authority=LOCAL_USER_AUTHORITY,
        )
        wait_any_second = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:wait-any",
            source=ExecutionTaskSource.SYSTEM,
            label="wait-any-second",
            authority=LOCAL_USER_AUTHORITY,
        )

        async def _complete_wait_any() -> None:
            await asyncio.sleep(0.02)
            await runtime.task_coordinator.mark_completed(wait_any_second.task_id)

        wait_any_completion = asyncio.create_task(_complete_wait_any())
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            wait_any = _decode(
                await invoke(
                    operation="wait",
                    job_ids=[wait_any_first.task_id, wait_any_second.task_id],
                    timeout_seconds=10.0,
                )
            )
        await wait_any_completion
        self.soft_assert_equal(
            [item["status"] for item in wait_any["jobs"]],
            ["queued", "completed"],
            "Job wait should return when any selected job becomes terminal",
        )

        attention_task = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:attention",
            source=ExecutionTaskSource.SYSTEM,
            label="attention-job",
            authority=LOCAL_USER_AUTHORITY,
        )

        async def _request_attention() -> None:
            await asyncio.sleep(0.02)
            await runtime.task_coordinator.publish_progress(
                attention_task.task_id,
                metadata={"attention_reason": "validation"},
                health_status="attention_required",
            )

        attention_update = asyncio.create_task(_request_attention())
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            attention_wait = _decode(
                await invoke(
                    operation="wait",
                    job_ids=[attention_task.task_id],
                    timeout_seconds=10.0,
                )
            )
        await attention_update
        self.soft_assert_equal(
            attention_wait["jobs"][0]["health_status"],
            "attention_required",
            "Job wait should wake when a selected job requests attention",
        )

        with use_execution_authority(
            ExecutionAuthority(principal_id="job-tool-principal")
        ):
            concealed_wait = _decode(
                await invoke(
                    operation="wait",
                    job_ids=[inaccessible.task_id, attention_task.task_id],
                    timeout_seconds=10.0,
                )
            )
        self.soft_assert_equal(
            [item.get("outcome") for item in concealed_wait["jobs"]],
            ["not_found", "not_found"],
            "Job wait should conceal every inaccessible task in a mixed batch",
        )

        cancellable = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:cancellable",
            source=ExecutionTaskSource.SYSTEM,
            label="cancellable-job",
            authority=LOCAL_USER_AUTHORITY,
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            cancelled = _decode(
                await invoke(operation="cancel", job_ids=[cancellable.task_id])
            )
        self.soft_assert_equal(
            cancelled["jobs"][0]["cancelled"],
            True,
            "Job cancellation should return one deterministic per-ID outcome",
        )
        bulk_one = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:bulk-cancel",
            source=ExecutionTaskSource.SYSTEM,
            label="bulk-one",
            authority=LOCAL_USER_AUTHORITY,
        )
        bulk_two = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:bulk-cancel",
            source=ExecutionTaskSource.SYSTEM,
            label="bulk-two",
            authority=LOCAL_USER_AUTHORITY,
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            bulk_cancelled = _decode(
                await invoke(
                    operation="cancel",
                    job_ids=[bulk_one.task_id, bulk_two.task_id],
                )
            )
        self.soft_assert_equal(
            [item["cancelled"] for item in bulk_cancelled["jobs"]],
            [True, True],
            "Job cancellation should process every supplied job ID",
        )

        delegate_only = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.DELEGATE,
            scope="delegate_parent:job-tool-session",
            source=ExecutionTaskSource.TOOL,
            label="delegate-only",
            authority=LOCAL_USER_AUTHORITY,
        )
        active_chat_response = self.call_api(
            "/api/chat/sessions/job-tool-session/active-task"
        )
        self.soft_assert_equal(
            active_chat_response.status_code,
            404,
            "A delegate child should not be mistaken for its active parent chat",
        )
        await runtime.task_coordinator.cancel_task(
            delegate_only.task_id,
            reason="validation_cleanup",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _runtime(self):
        from core.runtime.state import get_runtime_context

        return get_runtime_context()


def _decode(value: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(value))
