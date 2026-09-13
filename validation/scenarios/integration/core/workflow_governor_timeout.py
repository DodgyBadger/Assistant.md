"""Validate workflow timeout handling through the runtime task runner."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import core.runtime.workflow_governor as governor_module
from core.authoring.workflow_execution import WorkflowExecutionResult
from core.identity import LOCAL_USER_AUTHORITY
from core.runtime.background import RuntimeBackgroundSpawner
from core.runtime.execution_tasks import ExecutionTaskSource, TaskCoordinator
from core.runtime.task_runner import ExecutionTaskRunner
from core.runtime.workflow_governor import WorkflowGovernor
from core.workflow_runs import WorkflowRunStore
from validation.core.base_scenario import BaseScenario


class WorkflowGovernorTimeoutScenario(BaseScenario):
    """Validate workflow timeouts preserve workflow metadata and task status."""

    async def test_scenario(self):
        original_execute = governor_module.execute_workflow_by_id
        original_timeout = governor_module.get_workflow_task_timeout_seconds
        original_limit = governor_module.get_max_concurrent_workflows

        async def slow_execute_workflow_by_id(
            global_id: str,
            *,
            step_name: str | None = None,
            expect_failure: bool = False,
            include_load_errors: bool = False,
        ) -> WorkflowExecutionResult:
            del step_name, expect_failure, include_load_errors
            await asyncio.sleep(1)
            return WorkflowExecutionResult(
                success=True,
                global_id=global_id,
                status="completed",
                execution_time_seconds=1,
                output_files=[],
                reason=None,
                details=[],
                message="should not complete",
            )

        try:
            governor_module.execute_workflow_by_id = slow_execute_workflow_by_id
            governor_module.get_workflow_task_timeout_seconds = lambda: 0.01
            governor_module.get_max_concurrent_workflows = lambda: 0

            coordinator = TaskCoordinator()
            task_runner = ExecutionTaskRunner(
                task_coordinator=coordinator,
                background_spawner=RuntimeBackgroundSpawner(
                    background_loop=asyncio.get_running_loop()
                ),
            )
            run_store = WorkflowRunStore(str(self.artifacts_dir / "system"))
            governor = WorkflowGovernor(
                task_coordinator=coordinator,
                task_runner=task_runner,
                workflow_run_store=run_store,
            )
            result = await governor.execute_workflow(
                global_id="TimeoutVault/slow_probe",
                source=ExecutionTaskSource.SCHEDULER,
                authority=LOCAL_USER_AUTHORITY,
            )
            tasks = await coordinator.list_tasks(kind="workflow")
            durable_run = run_store.get_latest_run("TimeoutVault/slow_probe")
        finally:
            governor_module.execute_workflow_by_id = original_execute
            governor_module.get_workflow_task_timeout_seconds = original_timeout
            governor_module.get_max_concurrent_workflows = original_limit

        task = tasks[0] if tasks else None
        metadata = task.metadata if task is not None else {}
        workflow_result = metadata.get("workflow_result")
        workflow_failure = metadata.get("workflow_failure")

        self.soft_assert_equal(
            result.status, "timed_out", "Workflow result should report timeout"
        )
        self.soft_assert_equal(
            durable_run.status if durable_run else None,
            "timed_out",
            "Workflow timeout should persist as the latest durable outcome",
        )
        self.soft_assert_equal(
            task.status if task else None,
            "timed_out",
            "Workflow execution task should be marked timed_out",
        )
        self.soft_assert_equal(
            task.terminal_reason if task else None,
            "workflow_task_timeout:0.01s",
            "Workflow timeout should use the configured workflow timeout reason",
        )
        self.soft_assert(
            isinstance(workflow_result, dict),
            "Workflow timeout should store workflow_result metadata",
        )
        if isinstance(workflow_result, dict):
            self.soft_assert_equal(
                workflow_result.get("status"),
                "timed_out",
                "Workflow timeout result metadata should be timed_out",
            )
        self.soft_assert(
            isinstance(workflow_failure, dict),
            "Workflow timeout should store workflow_failure metadata",
        )
        if isinstance(workflow_failure, dict):
            self.soft_assert_equal(
                workflow_failure.get("failure_kind"),
                "workflow_timeout",
                "Workflow timeout failure metadata should be classified",
            )
            self.soft_assert_equal(
                workflow_failure.get("retryable"),
                False,
                "Workflow timeout failure metadata should not be retryable unchanged",
            )

        self.teardown_scenario()
        self.assert_no_failures()
