"""System status, health, templates, and workflow-load API services."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from core.advanced_shell import AdvancedShellConfig
from core.advanced_shell.preflight import AdvancedShellPreflightSnapshot
from core.authentication import AuthenticationMode
from core.authoring.template_discovery import (
    list_templates,
)
from core.runtime.state import RuntimeStateError, get_runtime_context
from core.scheduling.job_history import get_scheduler_job_history
from core.scheduling.system_jobs import SYSTEM_JOB_IDS
from core.settings import (
    validate_settings,
)
from core.settings.store import (
    get_general_settings,
)
from core.vault_state.models import VaultFile, VaultFileEvent
from core.vault_state.service import VaultStateService

from ..exceptions import SystemConfigurationError
from ..models import (
    AdvancedShellStatusInfo,
    ConfigurationIssueInfo,
    ConfigurationStatusInfo,
    SchedulerInfo,
    StatusResponse,
    SystemInfo,
    TemplateInfo,
    VaultInfo,
    WorkflowRunInfo,
)
from ..models import (
    ConfigurationError as APIConfigurationError,
)
from .shared import (
    get_vault_path as _get_vault_path,
)
from .shared import (
    get_workflow_loader as _get_workflow_loader,
)
from .shared import logger
from .workflows import (
    _project_latest_workflow_runs,
    get_configuration_errors,
    get_system_workflow_template_summaries,
    get_workflow_summaries,
)

# Global variable to track system startup time
_system_startup_time: datetime | None = None


def get_workflow_load_errors(
    *,
    vault_name: str | None = None,
    workflow_name: str | None = None,
) -> list[APIConfigurationError]:
    """Return workflow configuration errors, optionally filtered by vault/workflow."""
    errors = get_configuration_errors()
    if vault_name:
        errors = [error for error in errors if error.vault == vault_name]
    if workflow_name:
        errors = [error for error in errors if error.workflow_name == workflow_name]
    return errors


def set_system_startup_time(startup_time: datetime) -> None:
    """Set the system startup time for status reporting."""
    global _system_startup_time
    _system_startup_time = startup_time


def project_advanced_shell_status(
    config: AdvancedShellConfig,
    preflight: AdvancedShellPreflightSnapshot,
) -> AdvancedShellStatusInfo:
    """Project deployment configuration without identity or trust paths."""
    return AdvancedShellStatusInfo(
        execution_mode=config.execution_mode,
        host=config.host,
        port=config.port,
        user=config.user,
        readiness_state=preflight.state,
        readiness_message=preflight.message,
    )


def list_context_templates(vault_name: str) -> list[TemplateInfo]:
    """List available context templates for a given vault."""
    vault_path: str | None = None
    try:
        vault_path = _get_vault_path(vault_name)
    except Exception as exc:
        logger.warning(
            f"Vault path lookup failed for '{vault_name}', falling back to system templates only: {exc}"
        )

    templates = list_templates(Path(vault_path) if vault_path else None)
    results: list[TemplateInfo] = []
    for tmpl in templates:
        results.append(
            TemplateInfo(
                name=tmpl.name,
                source=tmpl.source,
                path=str(tmpl.path) if tmpl.path else None,
            )
        )
    return results


async def collect_vault_status() -> list[VaultInfo]:
    """
    Collect status information about all discovered vaults from cached data.

    Returns:
        List of VaultInfo objects with vault details

    Raises:
        SystemConfigurationError: If vault discovery fails
    """
    try:
        # Use cached vault info from workflow loader
        vault_data = _get_workflow_loader().get_vault_info()
        vault_state_summary = _collect_vault_state_summary()

        # Create VaultInfo objects from cached data
        vault_infos = []
        for vault_name, data in vault_data.items():
            state_summary = vault_state_summary.get(vault_name, {})
            vault_info = VaultInfo(
                name=vault_name,
                path=data["path"],
                workflow_count=len(data["workflows"]),
                workflows=data["workflows"],
                tracked_files=state_summary.get("tracked_files"),
                files_created_recent=state_summary.get("files_created_recent"),
                files_deleted_recent=state_summary.get("files_deleted_recent"),
                latest_vault_change_at=state_summary.get("latest_vault_change_at"),
            )
            vault_infos.append(vault_info)

        return vault_infos

    except Exception as e:
        error_msg = f"Failed to collect vault status: {str(e)}"
        raise SystemConfigurationError(error_msg) from e


def _collect_vault_state_summary() -> dict[str, dict[str, Any]]:
    """Return cheap vault-state summary fields keyed by current vault name."""
    summary: dict[str, dict[str, Any]] = {}
    recent_change_cutoff = datetime.now(UTC) - timedelta(days=7)
    try:
        service = VaultStateService()
        with service.SessionFactory() as session:
            file_rows = session.execute(
                select(VaultFile.vault_name, func.count())
                .where(VaultFile.deleted_at.is_(None))
                .group_by(VaultFile.vault_name)
            ).all()
            for vault_name, count in file_rows:
                summary.setdefault(vault_name, {})["tracked_files"] = int(count)

            change_rows = session.execute(
                select(
                    VaultFileEvent.vault_name, func.max(VaultFileEvent.observed_at)
                ).group_by(VaultFileEvent.vault_name)
            ).all()
            for vault_name, latest_change in change_rows:
                summary.setdefault(vault_name, {})[
                    "latest_vault_change_at"
                ] = latest_change

            recent_change_rows = session.execute(
                select(
                    VaultFileEvent.vault_name, VaultFileEvent.event_type, func.count()
                )
                .where(
                    VaultFileEvent.observed_at >= recent_change_cutoff,
                    VaultFileEvent.event_type.in_(("created", "deleted")),
                )
                .group_by(VaultFileEvent.vault_name, VaultFileEvent.event_type)
            ).all()
            for vault_name, event_type, count in recent_change_rows:
                key = (
                    "files_created_recent"
                    if event_type == "created"
                    else "files_deleted_recent"
                )
                summary.setdefault(vault_name, {})[key] = int(count)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to collect vault-state status summary",
            data={
                "event": "vault_state_status_summary_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
    return summary


def collect_scheduler_status(scheduler: Any | None = None) -> SchedulerInfo:
    """
    Collect status information about the scheduler using APScheduler methods.

    Args:
        scheduler: APScheduler instance (optional, will try to get from main if None)

    Returns:
        SchedulerInfo object with scheduler details and job information
    """
    try:
        runtime = None
        try:
            runtime = get_runtime_context()
        except RuntimeStateError:
            runtime = None

        # If no scheduler provided, get from runtime context
        if scheduler is None:
            if runtime is not None:
                scheduler = runtime.scheduler

        if scheduler is None:
            # Return default scheduler info if unavailable
            return SchedulerInfo(
                running=False, total_jobs=0, enabled_workflows=0, disabled_workflows=0
            )

        # Get scheduler status
        is_running = scheduler.running

        # Get detailed job information using APScheduler's get_jobs()
        jobs = scheduler.get_jobs()
        total_jobs = len(jobs)

        # Extract job details from APScheduler
        job_summaries = []
        for job in jobs:
            job_type = "system" if job.id in SYSTEM_JOB_IDS else "workflow"
            history = get_scheduler_job_history(job.id) or {}
            if job_type == "workflow" and runtime is not None:
                workflow_id = job.id.replace("__", "/")
                latest_run = runtime.workflow_run_store.get_latest_run(workflow_id)
                if latest_run is not None:
                    unhealthy = latest_run.status in {"failed", "timed_out", "missed"}
                    history = {
                        "last_run_time": latest_run.completed_at,
                        "last_status": latest_run.status,
                        "last_error": latest_run.reason if unhealthy else None,
                        "last_run_id": latest_run.run_id,
                        "last_run_source": latest_run.source,
                    }
            job_summary = {
                "id": job.id,
                "name": job.name,
                "job_type": job_type,
                "next_run_time": job.next_run_time,
                "last_run_time": history.get("last_run_time"),
                "last_status": history.get("last_status"),
                "last_error": history.get("last_error"),
                "last_run_id": history.get("last_run_id"),
                "last_run_source": history.get("last_run_source"),
                "trigger_type": type(job.trigger).__name__,
                "trigger_description": str(job.trigger),
                "max_instances": job.max_instances,
                "misfire_grace_time": job.misfire_grace_time,
            }
            job_summaries.append(job_summary)

        # Sort by next run time for better display
        job_summaries.sort(key=lambda x: x["next_run_time"] or datetime.max)

        # Remove redundant workflow counting - this will be done at the higher level using cached data
        scheduler_info = SchedulerInfo(
            running=is_running,
            total_jobs=total_jobs,
            enabled_workflows=0,  # Will be calculated elsewhere
            disabled_workflows=0,  # Will be calculated elsewhere
            job_details=job_summaries,  # Add rich job data
        )

        return scheduler_info

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to collect scheduler status",
            data={
                "event": "scheduler_status_collection_failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        return SchedulerInfo(
            running=False,
            total_jobs=0,
            enabled_workflows=0,
            disabled_workflows=0,
            job_details=[],
        )


def collect_system_health() -> SystemInfo:
    """
    Collect system health information.

    Returns:
        SystemInfo object with system health details
    """
    try:
        # Get startup time
        startup_time = _system_startup_time or datetime.now()

        runtime = get_runtime_context()
        workflow_loader = runtime.workflow_loader

        # Prefer explicit runtime timestamp, fall back to loader metadata.
        last_reload = runtime.last_config_reload
        if last_reload is None and hasattr(workflow_loader, "_last_loaded"):
            last_reload = workflow_loader._last_loaded

        # Get data root
        data_root = workflow_loader._data_root

        system_info = SystemInfo(
            startup_time=startup_time,
            last_config_reload=last_reload,
            data_root=data_root,
            public_url=(
                runtime.config.public_origin.value
                if runtime.config.public_origin is not None
                else None
            ),
            public_url_source=(
                "configured"
                if runtime.config.public_origin is not None
                else "unconfigured"
            ),
            public_url_recommended=runtime.config.public_origin is None,
        )

        return system_info

    except Exception:
        # Return safe defaults on error
        return SystemInfo(
            startup_time=datetime.now(),
            last_config_reload=None,
            data_root="/app/data",
            public_url=None,
            public_url_source="unconfigured",
            public_url_recommended=True,
        )


async def get_system_status(
    scheduler: Any | None = None,
    *,
    authentication_mode: AuthenticationMode = AuthenticationMode.DISABLED,
    advanced_shell_config: AdvancedShellConfig | None = None,
    advanced_shell_preflight: AdvancedShellPreflightSnapshot,
) -> StatusResponse:
    """
    Collect comprehensive system status information from cached data.

    Args:
        scheduler: APScheduler instance (optional)

    Returns:
        StatusResponse with complete system status

    Raises:
        SystemConfigurationError: If critical status collection fails
    """
    try:
        # Use cached data - no reloading
        # Collect vault information
        vaults = await collect_vault_status()

        # Collect scheduler status
        scheduler_info = collect_scheduler_status(scheduler)

        # Collect system health
        system_info = collect_system_health()

        total_vaults = len(vaults)
        total_workflows = sum(vault.workflow_count for vault in vaults)

        workflow_summaries = get_workflow_summaries()
        enabled_workflows = [
            summary for summary in workflow_summaries if summary.enabled
        ]
        disabled_workflows = [
            summary for summary in workflow_summaries if not summary.enabled
        ]
        system_workflow_templates = get_system_workflow_template_summaries()
        runtime = get_runtime_context()
        latest_workflow_runs = _project_latest_workflow_runs(
            workflow_summaries=workflow_summaries,
            system_workflow_templates=system_workflow_templates,
            latest_runs=runtime.workflow_run_store.list_latest_runs(),
        )

        scheduler_info.enabled_workflows = len(enabled_workflows)
        scheduler_info.disabled_workflows = len(disabled_workflows)

        # Get configuration errors and overall configuration health
        configuration_errors = get_configuration_errors()
        configuration_status_snapshot = validate_settings()
        default_model_value = None
        try:
            default_entry = get_general_settings().get("default_model")
            if default_entry and getattr(default_entry, "value", None):
                default_model_value = str(default_entry.value).strip() or None
        except Exception:
            default_model_value = None

        configuration_status = ConfigurationStatusInfo(
            issues=[
                ConfigurationIssueInfo(
                    name=issue.name,
                    message=issue.message,
                    severity=issue.severity,
                )
                for issue in configuration_status_snapshot.issues
            ],
            tool_availability=dict(configuration_status_snapshot.tool_availability),
            model_availability=dict(configuration_status_snapshot.model_availability),
            default_model=default_model_value,
        )

        shell_config = advanced_shell_config or AdvancedShellConfig.restricted_default()
        status_response = StatusResponse(
            vaults=vaults,
            scheduler=scheduler_info,
            system=system_info,
            total_vaults=total_vaults,
            total_workflows=total_workflows,
            enabled_workflows=enabled_workflows,
            disabled_workflows=disabled_workflows,
            system_workflow_templates=system_workflow_templates,
            workflow_runs={
                workflow_id: WorkflowRunInfo.model_validate(run)
                for workflow_id, run in latest_workflow_runs.items()
            },
            configuration_errors=configuration_errors,
            configuration_status=configuration_status,
            authentication_mode=authentication_mode,
            authentication_warning=(
                (
                    "Authentication is disabled. Every network peer that can reach "
                    "Assistant.md has full UI and API access, including the advanced "
                    "shell."
                    if shell_config.enabled
                    else "Authentication is disabled. Every network peer that can "
                    "reach Assistant.md has full UI and API access."
                )
                if authentication_mode is AuthenticationMode.DISABLED
                else None
            ),
            advanced_shell=project_advanced_shell_status(
                shell_config, advanced_shell_preflight
            ),
        )

        return status_response

    except Exception as e:
        error_msg = f"Failed to collect system status: {str(e)}"
        raise SystemConfigurationError(error_msg) from e
