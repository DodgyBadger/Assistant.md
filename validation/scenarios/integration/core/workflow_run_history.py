"""Validate durable workflow outcomes across governor and scheduler paths."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from apscheduler.events import EVENT_JOB_MISSED

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import core.runtime.workflow_governor as governor_module
import core.scheduling.job_history as job_history_module
from api.services import _project_latest_workflow_runs, collect_scheduler_status
from core.authoring.workflow_execution import WorkflowExecutionResult
from core.identity import LOCAL_USER_AUTHORITY
from core.runtime import state as runtime_state
from core.runtime.background import RuntimeBackgroundSpawner
from core.runtime.execution_tasks import ExecutionTaskSource, TaskCoordinator
from core.runtime.task_runner import ExecutionTaskRunner
from core.runtime.workflow_governor import WorkflowGovernor
from core.workflow_runs import WorkflowRunStore
from validation.core.base_scenario import BaseScenario


class WorkflowRunHistoryScenario(BaseScenario):
    """Prove workflow results are durable and domain-status authoritative."""

    async def test_scenario(self):
        system_root = self.artifacts_dir / "system"
        store = WorkflowRunStore(str(system_root))
        original_execute = governor_module.execute_workflow_by_id
        original_timeout = governor_module.get_workflow_task_timeout_seconds
        original_limit = governor_module.get_max_concurrent_workflows
        original_store_resolver = job_history_module._get_workflow_run_store

        async def returned_failure(
            global_id: str,
            *,
            step_name: str | None = None,
            expect_failure: bool = False,
            include_load_errors: bool = False,
        ) -> WorkflowExecutionResult:
            del step_name, expect_failure, include_load_errors
            return WorkflowExecutionResult(
                success=False,
                global_id=global_id,
                status="failed",
                execution_time_seconds=0.25,
                output_files=["output.md"],
                reason="script used a removed tool",
                details=[],
                message="Workflow returned a domain failure",
            )

        try:
            governor_module.execute_workflow_by_id = returned_failure
            governor_module.get_workflow_task_timeout_seconds = lambda: 0
            governor_module.get_max_concurrent_workflows = lambda: 0
            job_history_module._get_workflow_run_store = lambda: store

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
                workflow_run_store=store,
            )
            missing_owner_failed = False
            try:
                await governor.execute_workflow(
                    global_id="HistoryVault/missing_owner",
                    source=ExecutionTaskSource.SCHEDULER,
                )
            except RuntimeError:
                missing_owner_failed = True
            result = await governor.execute_workflow(
                global_id="HistoryVault/nightly_cleanup",
                source=ExecutionTaskSource.SCHEDULER,
                authority=LOCAL_USER_AUTHORITY,
            )
            tasks = await coordinator.list_tasks(kind="workflow")

            reloaded_store = WorkflowRunStore(str(system_root))
            latest = reloaded_store.get_latest_run("HistoryVault/nightly_cleanup")
            runs = reloaded_store.list_runs("HistoryVault/nightly_cleanup")

            async def raised_failure(*args, **kwargs) -> WorkflowExecutionResult:
                del args, kwargs
                raise RuntimeError("raised workflow failure")

            governor_module.execute_workflow_by_id = raised_failure
            raised_exception = None
            try:
                await governor.execute_workflow(
                    global_id="HistoryVault/raised_failure",
                    source=ExecutionTaskSource.SCHEDULER,
                    authority=LOCAL_USER_AUTHORITY,
                )
            except RuntimeError as exc:
                raised_exception = exc
            failed_event = SimpleNamespace(
                code=0,
                job_id="HistoryVault__raised_failure",
                scheduled_run_time=datetime.now(UTC),
                exception=raised_exception,
                retval=None,
            )
            failed_scheduler = _FakeScheduler("Workflow: HistoryVault/raised_failure")
            job_history_module.record_scheduler_job_event(
                failed_event,
                scheduler=failed_scheduler,
            )
            raised_runs = reloaded_store.list_runs("HistoryVault/raised_failure")

            scheduled_time = datetime(2026, 7, 16, 2, 0, tzinfo=UTC)
            missed_event = SimpleNamespace(
                code=EVENT_JOB_MISSED,
                job_id="HistoryVault__nightly_cleanup",
                scheduled_run_time=scheduled_time,
                exception=None,
                retval=None,
            )
            scheduler = _FakeScheduler("Workflow: HistoryVault/nightly_cleanup")
            job_history_module.record_scheduler_job_event(
                missed_event, scheduler=scheduler
            )
            job_history_module.record_scheduler_job_event(
                missed_event, scheduler=scheduler
            )
            after_miss = reloaded_store.list_runs("HistoryVault/nightly_cleanup")
            runtime_state.set_runtime_context(
                SimpleNamespace(
                    scheduler=scheduler,
                    workflow_run_store=reloaded_store,
                )
            )
            try:
                scheduler_status = collect_scheduler_status()
            finally:
                runtime_state.clear_runtime_context()

            old_time = datetime.now(UTC) - timedelta(days=120)
            old_run = reloaded_store.record_terminal_run(
                workflow_id="RetentionVault/quiet_workflow",
                workflow_name="quiet_workflow",
                vault_name="RetentionVault",
                source="scheduler",
                owner_principal_id="local-user",
                status="completed",
                completed_at=old_time,
            )
            retained_old_run = reloaded_store.get_run(old_run.run_id)
            reloaded_store.record_terminal_run(
                workflow_id="RetentionVault/quiet_workflow",
                workflow_name="quiet_workflow",
                vault_name="RetentionVault",
                source="scheduler",
                owner_principal_id="local-user",
                status="completed",
            )
            pruned_old_run = reloaded_store.get_run(old_run.run_id)

            template_old_time = datetime.now(UTC) - timedelta(minutes=2)
            reloaded_store.record_terminal_run(
                workflow_id="FirstVault/system/nightly-session-summarization",
                workflow_name="system/nightly-session-summarization",
                vault_name="FirstVault",
                source="api",
                owner_principal_id="local-user",
                status="completed",
                completed_at=template_old_time,
            )
            reloaded_store.record_terminal_run(
                workflow_id="SecondVault/system/nightly-session-summarization",
                workflow_name="system/nightly-session-summarization",
                vault_name="SecondVault",
                source="api",
                owner_principal_id="local-user",
                status="skipped",
                reason="no sessions pending",
            )
            template_history = reloaded_store.list_runs_by_workflow_name(
                "system/nightly-session-summarization"
            )
            projected_runs = _project_latest_workflow_runs(
                workflow_summaries=[],
                system_workflow_templates=[
                    SimpleNamespace(name="nightly-session-summarization")
                ],
                latest_runs=reloaded_store.list_latest_runs(),
            )
            projected_template_run = projected_runs.get(
                "system/nightly-session-summarization"
            )
        finally:
            governor_module.execute_workflow_by_id = original_execute
            governor_module.get_workflow_task_timeout_seconds = original_timeout
            governor_module.get_max_concurrent_workflows = original_limit
            job_history_module._get_workflow_run_store = original_store_resolver

        self.soft_assert_equal(
            result.status, "failed", "Probe should return a domain failure"
        )
        self.soft_assert(
            missing_owner_failed,
            "Scheduled workflow execution must fail closed without durable ownership",
        )
        self.soft_assert_equal(
            latest.owner_principal_id if latest else None,
            "local-user",
            "Durable workflow runs should retain their execution owner",
        )
        self.soft_assert_equal(
            latest.status if latest else None,
            "failed",
            "Returned workflow failure should be the durable latest outcome",
        )
        self.soft_assert_equal(
            latest.reason if latest else None,
            "script used a removed tool",
            "Durable failure should retain its concise reason",
        )
        self.soft_assert_equal(
            latest.output_files if latest else None,
            ("output.md",),
            "Durable outcome should retain bounded output paths",
        )
        self.soft_assert_equal(
            len(runs), 1, "One governor attempt should create one run"
        )
        self.soft_assert_equal(
            len(raised_runs),
            1,
            "Scheduler error events should not duplicate governor-recorded exceptions",
        )
        self.soft_assert_equal(
            raised_runs[0].status if raised_runs else None,
            "failed",
            "Raised workflow failures should remain durable failures",
        )
        self.soft_assert_equal(
            tasks[0].status if tasks else None,
            "failed",
            "Execution task status should agree with the durable domain outcome",
        )
        self.soft_assert_equal(
            [run.status for run in after_miss],
            ["missed", "failed"],
            "Repeated scheduler miss events should persist exactly once",
        )
        self.soft_assert_equal(
            {run.owner_principal_id for run in after_miss},
            {"local-user"},
            "Scheduler event outcomes should retain serialized workflow ownership",
        )
        job_details = scheduler_status.job_details
        self.soft_assert_equal(
            job_details[0].get("last_status") if job_details else None,
            "missed",
            "Scheduler status should project the durable latest workflow outcome",
        )
        self.soft_assert_equal(
            job_details[0].get("last_run_source") if job_details else None,
            "scheduler",
            "Scheduler status should retain the durable run source",
        )
        self.soft_assert(
            retained_old_run is not None,
            "Retention should preserve an old outcome while it is the workflow's latest",
        )
        self.soft_assert(
            pruned_old_run is None,
            "Retention should prune an expired outcome after a newer terminal run exists",
        )
        self.soft_assert_equal(
            [run.vault_name for run in template_history],
            ["SecondVault", "FirstVault"],
            "System template history should include attempts from every target vault",
        )
        self.soft_assert_equal(
            projected_template_run.get("status") if projected_template_run else None,
            "skipped",
            "System template rows should project the latest cross-vault outcome",
        )
        self.soft_assert_equal(
            (
                projected_template_run.get("vault_name")
                if projected_template_run
                else None
            ),
            "SecondVault",
            "System template latest outcomes should retain their target vault",
        )

        self.teardown_scenario()
        self.assert_no_failures()


class _FakeScheduler:
    def __init__(self, job_name: str) -> None:
        self._job_name = job_name

    def get_job(self, job_id: str) -> SimpleNamespace:
        return self._job(job_id)

    def get_jobs(self) -> list[SimpleNamespace]:
        return [self._job("HistoryVault__nightly_cleanup")]

    @property
    def running(self) -> bool:
        return True

    def _job(self, job_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            id=job_id,
            name=self._job_name,
            next_run_time=datetime(2026, 7, 17, 2, 0, tzinfo=UTC),
            trigger=SimpleNamespace(),
            max_instances=1,
            misfire_grace_time=60,
            args=({"owner_principal_id": "local-user"},),
        )
