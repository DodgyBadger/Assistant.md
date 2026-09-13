"""In-process scheduler job execution history."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from typing import Any

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED

from core.identity import LOCAL_USER_PRINCIPAL_ID
from core.logger import UnifiedLogger
from core.runtime.state import RuntimeStateError, get_runtime_context
from core.workflow_runs import WorkflowRunStore

_job_history: dict[str, dict[str, Any]] = {}
_lock = Lock()
logger = UnifiedLogger(tag="scheduler-jobs")


def attach_scheduler_history_listener(scheduler: Any) -> None:
    """Attach the process-local scheduler job history listener."""
    scheduler.add_listener(
        lambda event: record_scheduler_job_event(event, scheduler=scheduler),
        EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED,
    )


def record_scheduler_job_event(event: Any, *, scheduler: Any | None = None) -> None:
    """Record the last terminal event for one scheduler job."""
    job_id = getattr(event, "job_id", None)
    if not job_id:
        return

    exception = getattr(event, "exception", None)
    missed = getattr(event, "code", None) == EVENT_JOB_MISSED
    result = getattr(event, "retval", None)
    status = (
        "missed"
        if missed
        else str(
            getattr(result, "status", "") or ("error" if exception else "completed")
        )
    )
    row = {
        "last_run_time": datetime.now(UTC),
        "last_status": status,
        "last_error": str(exception) if exception else None,
    }
    with _lock:
        _job_history[str(job_id)] = row

    _record_durable_scheduler_outcome(
        event,
        scheduler=scheduler,
        status=status,
        missed=missed,
    )
    _log_scheduler_terminal_event(event, scheduler=scheduler, status=status)


def get_scheduler_job_history(job_id: str) -> dict[str, Any] | None:
    """Return the latest process-local execution history for a scheduler job."""
    with _lock:
        row = _job_history.get(job_id)
        return dict(row) if row else None


def _log_scheduler_terminal_event(
    event: Any,
    *,
    scheduler: Any | None,
    status: str,
) -> None:
    """Emit activity for workflow scheduler terminal events and scheduler errors."""
    job_id = str(getattr(event, "job_id", ""))
    job_name = _get_job_name(scheduler, job_id)
    workflow_id = _workflow_id_from_job(job_id, job_name)
    exception = getattr(event, "exception", None)

    if workflow_id is None and exception is None:
        return

    result = getattr(event, "retval", None)
    result_fields = _workflow_result_fields(result)
    scheduled_run_time = getattr(event, "scheduled_run_time", None)
    if scheduled_run_time is not None:
        try:
            scheduled_run_time = scheduled_run_time.isoformat()
        except Exception:
            scheduled_run_time = str(scheduled_run_time)

    logger.add_sink("validation").info(
        (
            "Scheduler job missed"
            if status == "missed"
            else (
                "Scheduler job completed"
                if exception is None
                else "Scheduler job failed"
            )
        ),
        data={
            "event": (
                "scheduler_job_missed"
                if status == "missed"
                else (
                    "scheduler_job_executed"
                    if exception is None
                    else "scheduler_job_error"
                )
            ),
            "job_id": job_id,
            "job_name": job_name,
            "workflow_id": workflow_id,
            "workflow_name": _workflow_name(workflow_id),
            "scheduled_run_time": scheduled_run_time,
            "status": status,
            "error_type": type(exception).__name__ if exception else None,
            "error": str(exception) if exception else None,
            **result_fields,
        },
    )


def _record_durable_scheduler_outcome(
    event: Any,
    *,
    scheduler: Any | None,
    status: str,
    missed: bool,
) -> None:
    """Persist scheduler outcomes that did not already finish in the governor."""
    job_id = str(getattr(event, "job_id", ""))
    job_name = _get_job_name(scheduler, job_id)
    workflow_id = _workflow_id_from_job(job_id, job_name)
    if workflow_id is None:
        return

    scheduled_run_time = _scheduled_run_time(event)
    event_key = (
        f"{job_id}:{'missed' if missed else 'error'}:{scheduled_run_time or 'unknown'}"
    )
    store = _get_workflow_run_store()
    owner_principal_id = _get_job_owner_principal_id(scheduler, job_id)
    if not missed:
        exception = getattr(event, "exception", None)
        if exception is None:
            return
        if getattr(exception, "assistantmd_workflow_run_id", None):
            return
        reason = f"{type(exception).__name__}: {exception}"
        store.record_terminal_run(
            workflow_id=workflow_id,
            workflow_name=_workflow_name(workflow_id) or workflow_id,
            vault_name=_workflow_vault(workflow_id),
            source="scheduler",
            owner_principal_id=owner_principal_id,
            status="failed",
            reason=reason,
            message=str(exception),
            scheduler_job_id=job_id,
            scheduler_event_key=event_key,
            scheduled_run_time=scheduled_run_time,
        )
        return

    store.record_terminal_run(
        workflow_id=workflow_id,
        workflow_name=_workflow_name(workflow_id) or workflow_id,
        vault_name=_workflow_vault(workflow_id),
        source="scheduler",
        owner_principal_id=owner_principal_id,
        status="missed",
        reason="scheduler_job_missed",
        message=f"Scheduled workflow '{workflow_id}' did not run",
        scheduler_job_id=job_id,
        scheduler_event_key=event_key,
        scheduled_run_time=scheduled_run_time,
    )


def _get_workflow_run_store() -> WorkflowRunStore:
    """Resolve the runtime-owned workflow store, with bootstrap-safe fallback."""
    try:
        return get_runtime_context().workflow_run_store
    except RuntimeStateError:
        return WorkflowRunStore()


def _get_job_owner_principal_id(scheduler: Any | None, job_id: str) -> str:
    """Read durable workflow ownership from serialized scheduler arguments."""
    job = scheduler.get_job(job_id) if scheduler is not None else None
    args = getattr(job, "args", ()) if job is not None else ()
    if args and isinstance(args[0], dict):
        owner = str(args[0].get("owner_principal_id") or "").strip()
        if owner:
            return owner
    # Existing persisted jobs predate the ownership field and all belong to the
    # installation's only interactive principal.
    return LOCAL_USER_PRINCIPAL_ID


def _scheduled_run_time(event: Any) -> str | None:
    value = getattr(event, "scheduled_run_time", None)
    if value is None:
        return None
    try:
        return str(value.isoformat())
    except Exception:
        return str(value)


def _workflow_vault(workflow_id: str) -> str:
    return workflow_id.split("/", 1)[0] if "/" in workflow_id else "unknown"


def _get_job_name(scheduler: Any | None, job_id: str) -> str | None:
    """Return the scheduler job name if the job is still available."""
    if scheduler is None:
        return None
    try:
        job = scheduler.get_job(job_id)
    except Exception:
        return None
    return str(job.name) if job is not None and job.name is not None else None


def _workflow_id_from_job(job_id: str, job_name: str | None) -> str | None:
    """Return a workflow id for workflow jobs, if this looks like one."""
    prefix = "Workflow: "
    if job_name and job_name.startswith(prefix):
        return job_name[len(prefix) :]
    if "__" in job_id:
        return job_id.replace("__", "/")
    return None


def _workflow_name(workflow_id: str | None) -> str | None:
    """Return the workflow name portion of a vault-scoped workflow id."""
    if not workflow_id:
        return None
    return workflow_id.split("/", 1)[1] if "/" in workflow_id else workflow_id


def _workflow_result_fields(result: Any) -> dict[str, Any]:
    """Extract compact WorkflowExecutionResult fields from a scheduler retval."""
    if result is None:
        return {}

    return {
        "workflow_status": getattr(result, "status", None),
        "workflow_reason": getattr(result, "reason", None),
        "workflow_execution_time_seconds": getattr(
            result, "execution_time_seconds", None
        ),
        "workflow_output_files": getattr(result, "output_files", None),
        "workflow_message": getattr(result, "message", None),
    }
