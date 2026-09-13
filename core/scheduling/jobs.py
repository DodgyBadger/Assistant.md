"""
Scheduler job management for dynamic workflow scheduling.
Handles job setup, updates, and lifecycle management for APScheduler.
"""

from typing import Any

from core.logger import UnifiedLogger
from core.runtime.state import get_runtime_context
from core.scheduling.system_jobs import SYSTEM_JOB_IDS

# Create scheduler job management logger
logger = UnifiedLogger(tag="scheduler-jobs")

RESERVED_JOB_IDS = SYSTEM_JOB_IDS


def _get_job_snapshot(scheduler: Any, job_id: str) -> dict[str, Any]:
    """Capture job metadata for validation events."""
    job = scheduler.get_job(job_id) if scheduler is not None else None
    if job is None:
        return {}

    next_run_time = None
    if job.next_run_time is not None:
        try:
            next_run_time = job.next_run_time.isoformat()
        except Exception:
            next_run_time = str(job.next_run_time)

    return {
        "job_name": job.name,
        "next_run_time": next_run_time,
    }


def _workflow_name(global_id: str) -> str:
    """Return the workflow name portion of a vault-scoped workflow id."""
    return global_id.split("/", 1)[1] if "/" in global_id else global_id


def _workflow_id_from_job_id(job_id: str) -> str:
    """Best-effort reconstruction of a workflow id from its scheduler job id."""
    return job_id.replace("__", "/")


def _workflow_summary(workflow: Any) -> dict[str, Any]:
    """Return a compact workflow identity record for activity filtering."""
    return {
        "workflow_id": workflow.global_id,
        "workflow_name": workflow.name,
        "vault": workflow.vault,
        "enabled": workflow.enabled,
        "run_type": workflow.run_type,
        "schedule": workflow.schedule_string,
    }


def _workflow_schedule_record(
    workflow: Any,
    *,
    scheduler: Any,
    action: str,
) -> dict[str, Any]:
    """Return a compact scheduler sync record for one workflow."""
    job_name = f"Workflow: {workflow.global_id}"
    snapshot = _get_job_snapshot(scheduler, workflow.scheduler_job_id)
    return {
        "workflow_id": workflow.global_id,
        "workflow_name": workflow.name,
        "vault": workflow.vault,
        "job_id": workflow.scheduler_job_id,
        "job_name": snapshot.get("job_name", job_name),
        "action": action,
        "trigger": str(workflow.trigger) if workflow.trigger is not None else None,
        "next_run_time": snapshot.get("next_run_time"),
        "run_type": workflow.run_type,
        "schedule": workflow.schedule_string,
    }


def _log_scheduler_change(message: str, record: dict[str, Any]) -> None:
    """Emit a per-workflow scheduler activity event for meaningful changes."""
    logger.add_sink("validation").info(
        message,
        data={
            "event": f"workflow_job_{record['action']}",
            **record,
        },
    )


def create_job_args(
    global_id: str,
    owner_principal_id: str,
    data_root: str | None = None,
    file_path: str | None = None,
) -> dict[str, Any]:
    """Create picklable job arguments for workflow execution.

    Returns lightweight, serializable arguments with flexible config dictionary.
    This eliminates memory waste, enables SQLAlchemy job store serialization,
    and provides extensible configuration interface.

    Args:
        global_id: Workflow identifier (vault/name)
        owner_principal_id: Principal that owns scheduled workflow execution
        data_root: Data root override (defaults to current runtime context data root)
        file_path: Absolute path to the workflow file (avoids reconstruction at runtime)
    """
    if data_root is None:
        runtime = get_runtime_context()
        data_root = str(runtime.config.data_root)

    return {
        "global_id": global_id,
        "owner_principal_id": owner_principal_id,
        "file_path": file_path,
        "config": {
            "data_root": str(data_root),
        },
    }


async def setup_scheduler_jobs(
    scheduler: Any, manual_reload: bool = False
) -> dict[str, Any]:
    """
    Set up or update scheduler jobs based on vault workflow configuration.

    Uses intelligent job synchronization to preserve timing state when possible:
    - If schedule/workflow unchanged: modify job args only (preserves timing)
    - If schedule/workflow changed: replace job completely (resets timing)
    - For new workflows: create new jobs

    Args:
        scheduler: APScheduler instance to update
        manual_reload: If True, manually triggered reload (e.g., from /rescan API)

    Returns:
        Dictionary with setup statistics:
        - vaults_discovered: Number of vaults found
        - workflows_loaded: Total workflows loaded
        - enabled_workflows: Number of enabled workflows
        - scheduler_jobs_synced: Number of jobs synchronized (includes no-change updates)

    Raises:
        Exception: If critical setup operations fail
    """
    runtime = get_runtime_context()
    workflow_loader = runtime.workflow_loader

    workflows = await workflow_loader.load_workflows(force_reload=manual_reload)
    enabled_workflows = workflow_loader.get_enabled_workflows()

    vaults_discovered = len({workflow.vault for workflow in workflows})
    workflows_loaded = len(workflows)
    enabled_workflows_count = len(enabled_workflows)
    scheduler_jobs_synced = 0
    created_count = 0
    replaced_count = 0
    unchanged_count = 0
    removed_count = 0
    scheduled_workflow_records: list[dict[str, Any]] = []
    unscheduled_enabled_records: list[dict[str, Any]] = []
    loaded_workflow_records = [_workflow_summary(workflow) for workflow in workflows]
    disabled_workflow_records = [
        {
            **_workflow_summary(workflow),
            "reason": "disabled",
        }
        for workflow in workflows
        if not workflow.enabled
    ]

    # Intelligent job synchronization for all enabled workflows
    if scheduler is not None:
        for workflow in enabled_workflows:
            if workflow.trigger is None:
                unscheduled_enabled_records.append(
                    {
                        **_workflow_summary(workflow),
                        "reason": "no_schedule",
                    }
                )
                continue

            await workflow_loader.ensure_workflow_directories(workflow)

            existing_job = scheduler.get_job(workflow.scheduler_job_id)

            if existing_job:
                schedule_changed = str(existing_job.trigger) != str(workflow.trigger)
                workflow_changed = existing_job.func != workflow.workflow_function

                if schedule_changed or workflow_changed:
                    scheduler.remove_job(existing_job.id)

                    job_args = create_job_args(
                        workflow.global_id,
                        workflow.owner_principal_id,
                        file_path=workflow.file_path,
                    )
                    job_name = f"Workflow: {workflow.global_id}"

                    scheduler.add_job(
                        func=workflow.workflow_function,
                        trigger=workflow.trigger,
                        args=[job_args],
                        id=workflow.scheduler_job_id,
                        name=job_name,
                    )

                    record = _workflow_schedule_record(
                        workflow,
                        scheduler=scheduler,
                        action="replaced",
                    )
                    scheduled_workflow_records.append(record)
                    replaced_count += 1
                    _log_scheduler_change(
                        "Workflow job replaced (schedule metadata changed)",
                        record,
                    )
                else:
                    job_args = create_job_args(
                        workflow.global_id,
                        workflow.owner_principal_id,
                        file_path=workflow.file_path,
                    )
                    job_name = f"Workflow: {workflow.global_id}"

                    scheduler.modify_job(
                        job_id=workflow.scheduler_job_id, args=[job_args], name=job_name
                    )

                    record = _workflow_schedule_record(
                        workflow,
                        scheduler=scheduler,
                        action="unchanged",
                    )
                    scheduled_workflow_records.append(record)
                    unchanged_count += 1

                scheduler_jobs_synced += 1
            else:
                job_args = create_job_args(
                    workflow.global_id,
                    workflow.owner_principal_id,
                    file_path=workflow.file_path,
                )
                job_name = f"Workflow: {workflow.global_id}"

                scheduler.add_job(
                    func=workflow.workflow_function,
                    trigger=workflow.trigger,
                    args=[job_args],
                    id=workflow.scheduler_job_id,
                    name=job_name,
                )

                record = _workflow_schedule_record(
                    workflow,
                    scheduler=scheduler,
                    action="created",
                )
                scheduled_workflow_records.append(record)
                created_count += 1
                _log_scheduler_change("Workflow job created", record)

                scheduler_jobs_synced += 1

        all_scheduler_job_ids = {
            job.id for job in scheduler.get_jobs() if job.id not in RESERVED_JOB_IDS
        }
        enabled_job_ids = {
            workflow.scheduler_job_id
            for workflow in enabled_workflows
            if workflow.trigger is not None
        }
        jobs_to_remove = all_scheduler_job_ids - enabled_job_ids

        for job_id in jobs_to_remove:
            snapshot = _get_job_snapshot(scheduler, job_id)
            workflow_id = _workflow_id_from_job_id(job_id)
            scheduler.remove_job(job_id)
            removed_count += 1
            logger.add_sink("validation").info(
                "Removed job for disabled workflow",
                data={
                    "event": "workflow_job_removed",
                    "job_id": job_id,
                    "job_name": snapshot.get("job_name"),
                    "workflow_id": workflow_id,
                    "workflow_name": _workflow_name(workflow_id),
                    "reason": "workflow_disabled_or_schedule_removed",
                },
            )
    else:
        pass  # Scheduler not available - configurations loaded but jobs not updated

    if scheduler is not None:
        summary_data = {
            "event": "workflow_scheduler_sync_completed",
            "manual_reload": manual_reload,
            "vaults_discovered": vaults_discovered,
            "workflows_loaded": workflows_loaded,
            "enabled_workflows": enabled_workflows_count,
            "scheduled_workflows_count": len(scheduled_workflow_records),
            "unscheduled_enabled_workflows_count": len(unscheduled_enabled_records),
            "disabled_workflows_count": len(disabled_workflow_records),
            "scheduler_jobs_synced": scheduler_jobs_synced,
            "created_count": created_count,
            "replaced_count": replaced_count,
            "unchanged_count": unchanged_count,
            "removed_count": removed_count,
        }
        logger.info(
            "Workflow scheduler sync completed",
            data=summary_data,
        )
        logger.set_sinks(["validation"]).info(
            "Workflow scheduler sync completed",
            data={
                **summary_data,
                "loaded_workflows": loaded_workflow_records,
                "scheduled_workflows": scheduled_workflow_records,
                "unscheduled_enabled_workflows": unscheduled_enabled_records,
                "disabled_workflows": disabled_workflow_records,
            },
        )

    # Prepare results
    results = {
        "vaults_discovered": vaults_discovered,
        "workflows_loaded": workflows_loaded,
        "enabled_workflows": enabled_workflows_count,
        "scheduler_jobs_synced": scheduler_jobs_synced,
        "scheduler_jobs_created": created_count,
        "scheduler_jobs_replaced": replaced_count,
        "scheduler_jobs_unchanged": unchanged_count,
        "scheduler_jobs_removed": removed_count,
    }

    return results
