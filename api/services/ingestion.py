"""API orchestration for ingestion jobs."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.constants import ASSISTANTMD_ROOT_DIR, IMPORT_DIR
from core.identity import require_current_execution_authority
from core.ingestion.import_service import ContentImportService, translate_ocr_options
from core.ingestion.jobs import (
    IngestionJob,
    cancel_queued_job,
    count_jobs,
    count_jobs_by_status,
    encode_job_cursor,
    find_job_for_source,
    list_jobs,
)
from core.ingestion.models import JobStatus, SourceKind
from core.ingestion.registry import importer_registry
from core.ingestion.service import IngestionService
from core.ingestion.task_execution import process_ingestion_job_now
from core.runtime.context import RuntimeContext
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


async def scan_import_folder(
    vault: str,
    queue_only: bool = False,
    strategies: list[str] | None = None,
    capture_ocr_images: bool | None = None,
    pdf_mode: str | None = None,
    ocr_options: dict[str, Any] | None = None,
) -> tuple[list[IngestionJob], list[str]]:
    """Enqueue supported import-folder files and optionally process them."""
    runtime, ingest_service, jobs_created, skipped = _enqueue_import_scan_jobs(
        vault=vault,
        strategies=strategies,
        capture_ocr_images=capture_ocr_images,
        pdf_mode=pdf_mode,
        ocr_options=ocr_options,
    )

    if not queue_only and jobs_created:
        refreshed_jobs: list[IngestionJob] = []
        for job in jobs_created:
            await process_ingestion_job_now(
                ingestion=ingest_service,
                task_coordinator=runtime.task_coordinator,
                job_id=job.id,
                source=ExecutionTaskSource.API,
                authority=require_current_execution_authority(),
            )
            refreshed_jobs.append(ingest_service.get_job(job.id) or job)
        jobs_created = refreshed_jobs

    logger.info(
        "Import scan completed",
        data={
            "vault": vault,
            "jobs_created": len(jobs_created),
            "skipped": len(skipped),
            "queue_only": queue_only,
        },
    )
    return jobs_created, skipped


def _enqueue_import_scan_jobs(
    *,
    vault: str,
    strategies: list[str] | None,
    capture_ocr_images: bool | None,
    pdf_mode: str | None,
    ocr_options: dict[str, Any] | None,
) -> tuple[RuntimeContext, IngestionService, list[IngestionJob], list[str]]:
    """Create ingestion jobs for supported files in a vault import folder."""
    runtime = get_runtime_context()
    vault_root = resolve_configured_vault_root(
        data_root=runtime.config.data_root, vault_name=vault
    )
    vault = vault_root.name
    import_root = vault_root / ASSISTANTMD_ROOT_DIR / IMPORT_DIR
    legacy_import_root = (
        Path(runtime.config.data_root) / vault / ASSISTANTMD_ROOT_DIR / "import"
    )
    import_root.mkdir(parents=True, exist_ok=True)

    ingest_service = runtime.ingestion
    jobs_created: list[IngestionJob] = []
    skipped: list[str] = []
    supported_exts = {key for key in importer_registry.keys() if key.startswith(".")}

    search_roots = [import_root]
    if legacy_import_root.exists():
        search_roots.append(legacy_import_root)

    extractor_options: dict[str, Any] = {}
    if capture_ocr_images is not None:
        extractor_options["ocr_capture_images"] = bool(capture_ocr_images)
    extractor_options.update(translate_ocr_options(ocr_options or {}))
    normalized_pdf_mode = (pdf_mode or "").strip().lower()
    if normalized_pdf_mode not in {"", "markdown", "page_images"}:
        normalized_pdf_mode = ""

    for root in search_roots:
        for item in sorted(root.iterdir()):
            if item.is_dir():
                continue
            if item.suffix.lower() not in supported_exts:
                skipped.append(item.name)
                continue
            source_uri = item.relative_to(vault_root).as_posix()
            existing_job = find_job_for_source(
                source_uri=source_uri,
                vault=vault,
                statuses=[JobStatus.QUEUED.value, JobStatus.PROCESSING.value],
            )
            if existing_job is None:
                existing_job = find_job_for_source(
                    source_uri=item.name,
                    vault=vault,
                    statuses=[JobStatus.QUEUED.value, JobStatus.PROCESSING.value],
                )
            if existing_job:
                skipped.append(item.name)
                continue

            job_options: dict[str, Any] = {}
            if strategies and item.suffix.lower() == ".pdf":
                job_options["strategies"] = strategies
            if extractor_options:
                job_options["extractor_options"] = extractor_options
            if normalized_pdf_mode:
                job_options["pdf_mode"] = normalized_pdf_mode

            jobs_created.append(
                ingest_service.enqueue_job(
                    source_uri=source_uri,
                    vault=vault,
                    source_type=SourceKind.FILE.value,
                    mime_hint=None,
                    options={**job_options, "consume_source": True},
                )
            )

    return runtime, ingest_service, jobs_created, skipped


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
    options: dict[str, Any] = {"clean_html": clean_html}
    if strategies:
        options["strategies"] = strategies
    if pdf_strategies:
        options["pdf_strategies"] = pdf_strategies
    if capture_ocr_images is not None:
        options["capture_ocr_images"] = capture_ocr_images
    if pdf_mode:
        options["pdf_mode"] = pdf_mode
    options.update(ocr_options or {})
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
