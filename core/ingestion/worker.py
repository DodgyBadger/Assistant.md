"""
APScheduler-driven worker to drain ingestion job queue.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from core.identity import LOCAL_USER_AUTHORITY
from core.ingestion.jobs import claim_queued_job, get_job, list_jobs, update_job_status
from core.ingestion.models import JobStatus
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    TaskCoordinator,
    ingestion_task_label,
    ingestion_vault_scope,
)
from core.runtime.task_runner import (
    ExecutionTaskHooks,
    ExecutionTaskRunner,
    ExecutionTaskSpec,
)


class IngestionWorker:
    def __init__(
        self,
        process_job_fn: Callable[[int], None],
        task_coordinator: TaskCoordinator,
        task_runner: ExecutionTaskRunner,
        max_concurrent: int = 1,
    ) -> None:
        self.process_job_fn = process_job_fn
        self.max_concurrent = max_concurrent
        self.task_coordinator = task_coordinator
        self.task_runner = task_runner
        self.logger = UnifiedLogger(tag="ingestion-worker")

    async def run_once(self) -> None:
        queued = [j for j in list_jobs(limit=100) if j.status == JobStatus.QUEUED.value]
        if not queued:
            return

        selected_jobs = queued[: self.max_concurrent]
        tracked_tasks = []
        for job in selected_jobs:
            if not claim_queued_job(job.id):
                continue
            try:
                tracked_tasks.append(await self._start_tracked_job(job.id))
            except Exception as exc:
                update_job_status(
                    job.id,
                    JobStatus.FAILED,
                    f"Failed to start ingestion task: {exc}",
                )
                self.logger.error(
                    "Failed to start ingestion task",
                    metadata={"job_id": job.id, "error": str(exc)},
                )
        await asyncio.gather(
            *(self._wait_for_task_terminal(task.task_id) for task in tracked_tasks)
        )

    async def _start_tracked_job(self, job_id: int) -> ExecutionTaskSnapshot:
        vault = self._job_vault(job_id)
        return await self.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.INGESTION,
                scope=ingestion_vault_scope(vault),
                source=ExecutionTaskSource.SCHEDULER,
                label=ingestion_task_label(job_id),
                authority=LOCAL_USER_AUTHORITY,
                metadata={"job_id": job_id, "vault": vault},
            ),
            lambda _task: asyncio.to_thread(self.process_job_fn, job_id),
            hooks=ExecutionTaskHooks(
                on_failed=lambda _task_id, exc: self._log_job_failure(job_id, exc),
            ),
        )

    def _job_vault(self, job_id: int) -> str:
        job = get_job(job_id)
        return job.vault if job is not None and job.vault else "unknown"

    async def _wait_for_task_terminal(self, task_id: str) -> None:
        while True:
            task = await self.task_coordinator.get_task(task_id)
            if task is None or task.is_terminal:
                return
            await asyncio.sleep(0.05)

    async def _log_job_failure(self, job_id: int, exc: BaseException) -> None:
        self.logger.error(
            "Failed to process ingestion job",
            metadata={"job_id": job_id, "error": str(exc)},
        )
