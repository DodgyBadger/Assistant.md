"""Administrative API services for retained application state."""

from datetime import datetime

from core.authoring.cache import purge_expired_cache_artifacts
from core.authoring.template_discovery import seed_system_templates
from core.goals import GoalOpsStore
from core.runtime.paths import get_system_root
from core.system_migrations import SystemMigrationStatus
from core.system_migrations import (
    get_system_migration_status as get_registered_system_migration_status,
)
from core.system_migrations import (
    run_system_migrations as run_registered_system_migrations,
)

from ..exceptions import SystemConfigurationError
from ..models import (
    CachePurgeResponse,
    GoalCleanupResponse,
    SystemMigrationRunResponse,
    SystemMigrationStatusResponse,
    SystemMigrationTargetInfo,
    SystemTemplateSeedResponse,
)
from .shared import logger


def purge_expired_cache() -> CachePurgeResponse:
    """Delete expired cache artifacts on demand."""
    now = datetime.now()
    purged_count = purge_expired_cache_artifacts(now=now)
    logger.info(
        "Manual cache purge completed",
        data={"purged_count": purged_count, "now": now.isoformat()},
    )
    return CachePurgeResponse(
        success=True,
        message=f"Purged {purged_count} expired cache artifact(s).",
        purged_count=purged_count,
    )


def cleanup_goals(
    vault_name: str,
    *,
    status: str,
    older_than_days: int | None,
) -> GoalCleanupResponse:
    """Delete old completed or cancelled goals for a vault."""
    status_key = (status or "completed").strip().lower()
    status_map = {
        "completed": ("completed",),
        "cancelled": ("cancelled",),
        "completed_or_cancelled": ("completed", "cancelled"),
    }
    statuses = status_map.get(status_key)
    if statuses is None:
        raise ValueError(
            "status must be completed, cancelled, or completed_or_cancelled"
        )

    deleted = GoalOpsStore().purge_goals(
        vault_name=vault_name,
        statuses=statuses,
        older_than_days=older_than_days,
    )
    logger.info(
        "Manual goal cleanup completed",
        data={
            "vault_name": vault_name,
            "status": status_key,
            "older_than_days": older_than_days,
            "deleted": deleted,
        },
    )
    if deleted == 0:
        message = "No goals matched."
    elif deleted == 1:
        message = "Deleted 1 goal."
    else:
        message = f"Deleted {deleted} goals."
    return GoalCleanupResponse(success=True, deleted=deleted, message=message)


def get_system_database_migration_status() -> SystemMigrationStatusResponse:
    """Return registered system database migration status."""
    try:
        status = get_registered_system_migration_status(get_system_root())
    except Exception as exc:
        raise SystemConfigurationError(
            f"Failed to inspect system database migrations: {exc}"
        ) from exc
    return _build_system_migration_status_response(status)


def run_system_database_migrations(backup: bool = True) -> SystemMigrationRunResponse:
    """Run registered system database migrations on demand."""
    try:
        status = run_registered_system_migrations(get_system_root(), backup=backup)
    except Exception as exc:
        raise SystemConfigurationError(
            f"Failed to run system database migrations: {exc}"
        ) from exc

    backups_created = [
        target.backup_path for target in status.targets if target.backup_path
    ]
    message = (
        "System database migrations completed."
        if status.pending_count == 0
        else f"System database migrations completed with {status.pending_count} migration(s) still pending."
    )
    logger.info(
        "Manual system database migration run completed",
        data={
            "pending_count": status.pending_count,
            "backups_created": len(backups_created),
            "backup": backup,
        },
    )
    response = _build_system_migration_status_response(status, message=message)
    return SystemMigrationRunResponse(
        **response.model_dump(),
        backups_created=backups_created,
    )


def _build_system_migration_status_response(
    status: SystemMigrationStatus,
    *,
    message: str | None = None,
) -> SystemMigrationStatusResponse:
    pending_count = status.pending_count
    summary = message or (
        "All registered system database migrations are applied."
        if pending_count == 0
        else f"{pending_count} system database migration(s) pending."
    )
    if any(target.inspection_error for target in status.targets):
        summary = (
            "Access-state migration status is unavailable while secrets are locked. "
            f"{pending_count} migration(s) pending in inspected databases."
        )
    return SystemMigrationStatusResponse(
        success=True,
        message=summary,
        system_root=status.system_root,
        pending_count=pending_count,
        targets=[
            SystemMigrationTargetInfo(
                db_name=target.db_name,
                namespace=target.namespace,
                db_path=target.db_path,
                exists=target.exists,
                applied_versions=list(target.applied_versions),
                pending_versions=list(target.pending_versions),
                backup_path=target.backup_path,
                inspection_error=target.inspection_error,
            )
            for target in status.targets
        ],
    )


def refresh_system_authoring_templates() -> SystemTemplateSeedResponse:
    """Refresh packaged system Authoring templates on demand."""
    try:
        result = seed_system_templates(get_system_root(), overwrite=True)
    except Exception as exc:
        raise SystemConfigurationError(
            f"Failed to refresh system authoring templates: {exc}"
        ) from exc

    created = result.get("created", [])
    updated = result.get("updated", [])
    skipped = result.get("skipped", [])
    errors = result.get("errors", [])
    success = bool(result.get("success", False))
    logger.info(
        "Manual system authoring template refresh completed",
        data={
            "created": len(created),
            "updated": len(updated),
            "skipped": len(skipped),
            "errors": len(errors),
            "success": success,
        },
    )
    message = (
        "System authoring templates refreshed: "
        f"{len(created)} created, {len(updated)} updated, {len(skipped)} skipped."
    )
    if errors:
        message += f" {len(errors)} error(s) occurred."
    return SystemTemplateSeedResponse(
        success=success,
        message=message,
        created=created,
        updated=updated,
        skipped=skipped,
        errors=errors,
    )
