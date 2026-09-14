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
        self.create_vault("JobToolVault")
        await self.start_system()
        runtime = self._runtime()

        from core.tools.job import Job

        job_tool = Job.get_tool()
        invoke = cast(Any, job_tool.function)

        owned = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="job-tool:owned",
            source=ExecutionTaskSource.SYSTEM,
            label="owned-job",
            authority=LOCAL_USER_AUTHORITY,
            metadata={"active_tools": ["file_read"]},
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
        self.soft_assert_equal(
            concealed["jobs"][0]["outcome"],
            "not_found",
            "Job status should conceal inaccessible task IDs as not found",
        )

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            listed = _decode(
                await invoke(
                    operation="list",
                    kind=ExecutionTaskKind.CHAT.value,
                    include_terminal=False,
                )
            )
            status = _decode(await invoke(operation="status", job_ids=[owned.task_id]))

        listed_ids = {item["job_id"] for item in listed["jobs"]}
        self.soft_assert_equal(
            owned.task_id in listed_ids,
            True,
            "Job list should expose accessible process-local jobs",
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

        import core.tools.job as job_module

        original_minimum = job_module.JOB_WAIT_MIN_SECONDS
        job_module.JOB_WAIT_MIN_SECONDS = 0.001
        try:
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                timer = _decode(await invoke(operation="wait", timeout_seconds=0.002))
        finally:
            job_module.JOB_WAIT_MIN_SECONDS = original_minimum
        self.soft_assert_equal(
            timer["timer_completed"],
            True,
            "Job wait without IDs should act as a plain timer",
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

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _runtime(self):
        from core.runtime.state import get_runtime_context

        return get_runtime_context()


def _decode(value: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(value))
