"""Validate workflow governor queueing through the runtime task gate."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

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


class WorkflowGovernorQueueScenario(BaseScenario):
    """Validate workflow vault lanes and optional global concurrency."""

    async def test_scenario(self):
        original_execute = governor_module.execute_workflow_by_id
        original_timeout = governor_module.get_workflow_task_timeout_seconds
        original_limit = governor_module.get_max_concurrent_workflows

        try:
            governor_module.get_workflow_task_timeout_seconds = lambda: 0.0

            governor_module.get_max_concurrent_workflows = lambda: 0
            history_root = self.artifacts_dir / "workflow-history"
            same_vault = await _run_probe(
                ("VaultA/first", "VaultA/second"),
                system_root=history_root,
            )
            self.soft_assert_equal(
                same_vault["max_active"],
                1,
                "Same-vault workflows should run sequentially",
            )
            self.soft_assert_equal(
                same_vault["statuses"],
                ["completed", "completed"],
                "Same-vault queued workflows should both complete",
            )
            self.soft_assert(
                "start:VaultA/second" in same_vault["timeline"]
                and same_vault["timeline"].index("start:VaultA/second")
                > same_vault["timeline"].index("end:VaultA/first"),
                "Second same-vault workflow should start after the first ends",
            )

            different_vaults = await _run_probe(
                ("VaultA/first", "VaultB/first"),
                system_root=history_root,
            )
            self.soft_assert_equal(
                different_vaults["max_active"],
                2,
                "Different-vault workflows should run concurrently when no global limit is set",
            )

            governor_module.get_max_concurrent_workflows = lambda: 1
            global_limited = await _run_probe(
                ("VaultA/first", "VaultB/first"),
                system_root=history_root,
            )
            self.soft_assert_equal(
                global_limited["max_active"],
                1,
                "Global workflow concurrency limit should serialize across vaults",
            )
        finally:
            governor_module.execute_workflow_by_id = original_execute
            governor_module.get_workflow_task_timeout_seconds = original_timeout
            governor_module.get_max_concurrent_workflows = original_limit

        self.teardown_scenario()
        self.assert_no_failures()


async def _run_probe(
    global_ids: tuple[str, str], *, system_root: Path
) -> dict[str, Any]:
    active = 0
    max_active = 0
    timeline: list[str] = []
    active_lock = asyncio.Lock()

    async def fake_execute_workflow_by_id(
        global_id: str,
        *,
        step_name: str | None = None,
        expect_failure: bool = False,
        include_load_errors: bool = False,
    ) -> WorkflowExecutionResult:
        nonlocal active, max_active
        del step_name, expect_failure, include_load_errors
        async with active_lock:
            active += 1
            max_active = max(max_active, active)
            timeline.append(f"start:{global_id}")
        await asyncio.sleep(0.05)
        async with active_lock:
            timeline.append(f"end:{global_id}")
            active -= 1
        return WorkflowExecutionResult(
            success=True,
            global_id=global_id,
            status="completed",
            execution_time_seconds=0.05,
            output_files=[],
            reason=None,
            details=[],
            message=f"completed {global_id}",
        )

    governor_module.execute_workflow_by_id = fake_execute_workflow_by_id
    coordinator = TaskCoordinator()
    task_runner = ExecutionTaskRunner(
        task_coordinator=coordinator,
        background_spawner=RuntimeBackgroundSpawner(
            background_loop=asyncio.get_running_loop()
        ),
    )
    governor = WorkflowGovernor(
        task_coordinator=coordinator,
        task_runner=task_runner,
        workflow_run_store=WorkflowRunStore(str(system_root)),
    )

    results = await asyncio.gather(
        *(
            governor.execute_workflow(
                global_id=global_id,
                source=ExecutionTaskSource.SCHEDULER,
                authority=LOCAL_USER_AUTHORITY,
            )
            for global_id in global_ids
        )
    )
    tasks = await coordinator.list_tasks(kind="workflow")
    return {
        "global_ids": list(global_ids),
        "statuses": [result.status for result in results],
        "max_active": max_active,
        "timeline": timeline,
        "task_statuses": [task.status for task in tasks],
    }
