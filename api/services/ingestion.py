"""API orchestration for ingestion jobs."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.identity import ExecutionAuthority, require_current_execution_authority
from core.ingestion.import_service import (
    ContentImportService,
)
from core.ingestion.jobs import (
    IngestionJob,
    cancel_queued_job,
    count_jobs,
    count_jobs_by_status,
    encode_job_cursor,
    list_jobs,
)
from core.ingestion.models import JobStatus, SourceKind
from core.ingestion.task_execution import process_ingestion_job_now
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context
from core.scheduling.system_jobs import INGESTION_WORKER_JOB_ID
from core.vault_state.pathing import resolve_configured_vault_root

from .shared import logger


def list_recent_import_jobs(
    *,
    limit: int = 25,
    vault: str | None = None,
    statuses: list[JobStatus] | None = None,
    cursor: str | None = None,
) -> tuple[list[IngestionJob], str | None, int, dict[str, int]]:
    """Return recent durable ingestion jobs for Dashboard visibility."""
    normalized_vault = (vault or "").strip() or None
    jobs = list_jobs(
        limit=limit + 1,
        vault=normalized_vault,
        statuses=statuses,
        cursor=cursor,
    )
    has_older = len(jobs) > limit
    page = jobs[:limit]
    status_counts = count_jobs_by_status(vault=normalized_vault)
    total_matching = (
        sum(status_counts.get(status.value, 0) for status in statuses)
        if statuses
        else sum(status_counts.values())
    )
    next_cursor = encode_job_cursor(page[-1].id) if has_older and page else None
    return page, next_cursor, total_matching, status_counts


def cancel_import_job(job_id: int) -> IngestionJob:
    """Cancel one job only while it remains queued."""
    job = cancel_queued_job(job_id)
    logger.add_sink("validation").info(
        "ingestion_job_cancelled",
        data={
            "event": "ingestion_job_cancelled",
            "job_id": job.id,
            "vault_name": job.vault,
            "status": job.status,
            "source": "api",
        },
    )
    return job


def get_import_job(job_id: int) -> IngestionJob:
    """Return one durable import job or raise a stable not-found error."""
    job = get_runtime_context().ingestion.get_job(job_id)
    if job is None:
        raise ValueError(f"Import job {job_id} not found")
    return job


async def process_import_jobs_background(
    job_ids: list[int], authority: ExecutionAuthority
) -> None:
    """Process accepted interactive import jobs after the response is sent."""
    runtime = get_runtime_context()
    for job_id in job_ids:
        await process_ingestion_job_now(
            ingestion=runtime.ingestion,
            task_coordinator=runtime.task_coordinator,
            job_id=job_id,
            source=ExecutionTaskSource.API,
            authority=authority,
        )


def trigger_import_queue_now() -> tuple[int, datetime]:
    """Advance the scheduler-owned ingestion worker to run now."""
    runtime = get_runtime_context()
    scheduler_job = runtime.scheduler.get_job(INGESTION_WORKER_JOB_ID)
    if scheduler_job is None:
        raise RuntimeError("Ingestion worker scheduler job is unavailable")
    queued_count = count_jobs(status=JobStatus.QUEUED)
    triggered_at = datetime.now(UTC)
    runtime.scheduler.modify_job(
        INGESTION_WORKER_JOB_ID,
        next_run_time=triggered_at,
    )
    runtime.scheduler.wakeup()
    logger.add_sink("validation").info(
        "ingestion_worker_triggered",
        data={
            "event": "ingestion_worker_triggered",
            "queued_count": queued_count,
            "source": "api",
            "triggered_at": triggered_at.isoformat(),
        },
    )
    return queued_count, triggered_at


async def import_url_direct(
    vault: str,
    url: str,
    clean_html: bool = True,
    strategies: list[str] | None = None,
    pdf_strategies: list[str] | None = None,
    capture_ocr_images: bool | None = None,
    pdf_mode: str | None = None,
    ocr_options: dict[str, Any] | None = None,
) -> IngestionJob:
    """Import one URL immediately as API-attributed ingestion."""
    runtime = get_runtime_context()
    ingest_service = runtime.ingestion
    import_service = ContentImportService(str(Path(runtime.config.data_root) / vault))
    options = _direct_import_options(
        clean_html=clean_html,
        strategies=strategies,
        pdf_strategies=pdf_strategies,
        capture_ocr_images=capture_ocr_images,
        pdf_mode=pdf_mode,
        ocr_options=ocr_options,
    )
    submitted = import_service.submit(sources=url, options=options)
    job = ingest_service.get_job(submitted[0].job_id)
    if job is None:
        raise RuntimeError("URL import job was not created")
    await process_ingestion_job_now(
        ingestion=ingest_service,
        task_coordinator=runtime.task_coordinator,
        job_id=job.id,
        source=ExecutionTaskSource.API,
        authority=require_current_execution_authority(),
    )
    refreshed_job = ingest_service.get_job(job.id) or job
    outputs = refreshed_job.outputs
    logger.info(
        "Import URL completed",
        data={
            "vault": vault,
            "status": refreshed_job.status,
            "outputs_count": len(outputs) if outputs is not None else 0,
            "clean_html": clean_html,
            "strategies": strategies or [],
            "pdf_strategies": pdf_strategies or [],
            "capture_ocr_images": capture_ocr_images,
            "pdf_mode": pdf_mode,
        },
    )
    return refreshed_job


async def import_sources_direct(
    *,
    vault: str,
    sources: list[str],
    destination: str | None = None,
    queue_only: bool = False,
    clean_html: bool = True,
    strategies: list[str] | None = None,
    pdf_strategies: list[str] | None = None,
    capture_ocr_images: bool | None = None,
    pdf_mode: str | None = None,
    ocr_options: dict[str, Any] | None = None,
) -> list[IngestionJob]:
    """Submit validated vault-file or URL sources to the ingestion queue."""
    runtime = get_runtime_context()
    vault_root = resolve_configured_vault_root(
        data_root=runtime.config.data_root,
        vault_name=vault,
    )
    import_service = ContentImportService(str(vault_root))
    options = _direct_import_options(
        destination=destination,
        clean_html=clean_html,
        strategies=strategies,
        pdf_strategies=pdf_strategies,
        capture_ocr_images=capture_ocr_images,
        pdf_mode=pdf_mode,
        ocr_options=ocr_options,
    )

    submitted = import_service.submit(sources=sources, options=options)
    jobs: list[IngestionJob] = []
    for item in submitted:
        job = runtime.ingestion.get_job(item.job_id)
        if job is None:
            raise RuntimeError(f"Import job {item.job_id} was not created")
        jobs.append(job)

    logger.info(
        "Direct content import submitted",
        data={
            "event": "ingestion_jobs_submitted",
            "status": "queued",
            "vault_name": vault_root.name,
            "source": "api",
            "job_ids": [job.id for job in jobs],
            "accepted_count": len(jobs),
            "url_count": sum(job.source_type == SourceKind.URL.value for job in jobs),
            "vault_file_count": sum(
                job.source_type == SourceKind.FILE.value for job in jobs
            ),
            "queue_only": queue_only,
            "destination": destination,
        },
    )
    return jobs


def _direct_import_options(
    *,
    clean_html: bool,
    destination: str | None = None,
    strategies: list[str] | None = None,
    pdf_strategies: list[str] | None = None,
    capture_ocr_images: bool | None = None,
    pdf_mode: str | None = None,
    ocr_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the shared public option payload for direct imports."""
    options: dict[str, Any] = {"clean_html": clean_html}
    if destination is not None:
        options["destination"] = destination
    if strategies:
        options["strategies"] = strategies
    if pdf_strategies:
        options["pdf_strategies"] = pdf_strategies
    if capture_ocr_images is not None:
        options["capture_ocr_images"] = capture_ocr_images
    if pdf_mode:
        options["pdf_mode"] = pdf_mode
    options.update(ocr_options or {})
    return options
