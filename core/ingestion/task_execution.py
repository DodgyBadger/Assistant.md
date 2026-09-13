"""Execution-task wrappers for ingestion jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from core.identity import ExecutionAuthority
from core.ingestion.jobs import IngestionJob
from core.ingestion.models import JobStatus
from core.ingestion.service import IngestionService
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSource,
    TaskCoordinator,
    ingestion_task_label,
    ingestion_vault_scope,
)

logger = UnifiedLogger(tag="ingestion-task-execution")

_TERMINAL_JOB_STATUSES = {
    JobStatus.COMPLETED.value,
    JobStatus.FAILED.value,
    JobStatus.CANCELLED.value,
}


async def process_ingestion_job_in_task(
    *,
    task_coordinator: TaskCoordinator,
    process_job_fn: Callable[[int], None],
    job_id: int,
    vault: str,
    source: ExecutionTaskSource,
    authority: ExecutionAuthority,
) -> None:
    """Run one ingestion job in a task context that vault mutations can audit."""
    async with task_coordinator.track_current_task(
        kind=ExecutionTaskKind.INGESTION,
        scope=ingestion_vault_scope(vault),
        source=source,
        label=ingestion_task_label(job_id),
        authority=authority,
        metadata={"job_id": job_id, "vault": vault},
    ):
        await asyncio.to_thread(process_job_fn, job_id)


async def process_ingestion_job_now(
    *,
    ingestion: IngestionService,
    task_coordinator: TaskCoordinator,
    job_id: int,
    source: ExecutionTaskSource,
    authority: ExecutionAuthority,
) -> IngestionJob:
    """Claim, process, and return one durable ingestion job inline."""
    submitted = ingestion.get_job(job_id)
    if submitted is None:
        raise ValueError(f"Ingestion job {job_id} not found")
    if not submitted.vault:
        raise ValueError(f"Ingestion job {job_id} is missing its vault")
    vault = submitted.vault

    claimed = ingestion.claim_job(job_id)
    if not claimed:
        current = ingestion.get_job(job_id)
        if current is None:
            raise ValueError(f"Ingestion job {job_id} not found")
        if current.status not in _TERMINAL_JOB_STATUSES:
            if current.status != JobStatus.PROCESSING.value:
                raise RuntimeError(
                    f"Ingestion job {job_id} could not be claimed from "
                    f"status {current.status}"
                )
            logger.info(
                "Waiting for already claimed ingestion job",
                data={
                    "event": "ingestion_immediate_wait_started",
                    "job_id": job_id,
                    "vault_name": vault,
                    "source": source.value,
                    "status": current.status,
                },
            )
            return await _wait_for_terminal_job(ingestion, job_id)
        return current

    try:
        await process_ingestion_job_in_task(
            task_coordinator=task_coordinator,
            process_job_fn=ingestion.process_job,
            job_id=job_id,
            vault=vault,
            source=source,
            authority=authority,
        )
    except Exception as exc:
        # process_job persists its own failures. If task setup failed before the
        # processor ran, close the claimed state instead of stranding it.
        current = ingestion.get_job(job_id)
        if current is not None and current.status == JobStatus.PROCESSING.value:
            ingestion.mark_failed(
                job_id, f"Failed to start immediate ingestion task: {exc}"
            )
        logger.error(
            "Immediate ingestion task failed",
            data={
                "event": "ingestion_immediate_processing_failed",
                "job_id": job_id,
                "vault_name": vault,
                "source": source.value,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )

    refreshed = ingestion.get_job(job_id)
    if refreshed is None:
        raise RuntimeError(f"Ingestion job {job_id} disappeared during processing")
    return refreshed


async def _wait_for_terminal_job(
    ingestion: IngestionService,
    job_id: int,
) -> IngestionJob:
    """Wait for another worker's claimed ingestion job to become terminal."""
    while True:
        current = ingestion.get_job(job_id)
        if current is None:
            raise RuntimeError(f"Ingestion job {job_id} disappeared during processing")
        if current.status in _TERMINAL_JOB_STATUSES:
            return current
        await asyncio.sleep(0.05)
