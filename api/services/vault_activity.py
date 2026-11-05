"""API projections for durable vault activity."""

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from core.runtime.state import get_runtime_context
from core.vault_state.activity_rollback import (
    ActivityRollbackPlan,
    ActivityRollbackUnavailable,
    execute_activity_rollback,
    preview_activity_rollback,
)
from core.vault_state.cleanup import cleanup_expired_vault_state
from core.vault_state.file_mutations import VaultMutationRejected
from core.vault_state.service import VaultActivityGroup, VaultStateService

from ..exceptions import APIException
from ..models import (
    VaultActivityGroupInfo,
    VaultActivityResponse,
    VaultActivityRollbackIssueInfo,
    VaultActivityRollbackPathInfo,
    VaultActivityRollbackPreviewResponse,
    VaultActivityRollbackResponse,
    VaultMutationInfo,
    VaultStateCleanupResponse,
)
from .shared import get_vault_path


@dataclass(frozen=True)
class SnapshotFileResponse:
    """Resolved snapshot artifact for HTTP serving."""

    path: Path
    filename: str
    media_type: str


def get_vault_activity(
    *,
    vault_name: str,
    limit: int = 50,
    task_id: str | None = None,
    include_expired: bool = False,
    operation: str | None = None,
) -> VaultActivityResponse:
    """Return durable attributed vault activity for one vault."""
    get_vault_path(vault_name)
    groups = _vault_state_service().list_activities(
        vault_name=vault_name,
        limit=limit,
        task_id=task_id,
        include_expired=include_expired,
        operation=operation,
    )
    return VaultActivityResponse(
        vault_name=vault_name,
        groups=[vault_activity_group_info(group) for group in groups],
    )


def get_vault_activity_rollback_preview(
    *,
    vault_name: str,
    activity_id: str,
) -> VaultActivityRollbackPreviewResponse:
    """Return current all-or-nothing rollback availability for one activity."""
    vault_root = get_vault_path(vault_name)
    try:
        plan = preview_activity_rollback(
            vault_path=vault_root,
            activity_id=activity_id,
        )
    except LookupError as exc:
        raise APIException(
            status_code=404,
            error_type="VaultActivityNotFound",
            message=str(exc),
            details={"vault_name": vault_name, "activity_id": activity_id},
        ) from exc
    return _vault_activity_rollback_preview_info(plan)


def rollback_vault_activity(
    *,
    vault_name: str,
    activity_id: str,
    expected_states: list[tuple[str, bool, str | None]],
) -> VaultActivityRollbackResponse:
    """Restore every supported path in one activity to its first before-state."""
    vault_root = get_vault_path(vault_name)
    try:
        result = execute_activity_rollback(
            vault_path=vault_root,
            activity_id=activity_id,
            expected_states=expected_states,
        )
    except LookupError as exc:
        raise APIException(
            status_code=404,
            error_type="VaultActivityNotFound",
            message=str(exc),
            details={"vault_name": vault_name, "activity_id": activity_id},
        ) from exc
    except ActivityRollbackUnavailable as exc:
        preview = _vault_activity_rollback_preview_info(exc.plan)
        raise APIException(
            status_code=409,
            error_type="VaultActivityRollbackUnavailable",
            message="Activity rollback is not currently available.",
            details=preview.model_dump(mode="json"),
        ) from exc
    except VaultMutationRejected as exc:
        raise APIException(
            status_code=409,
            error_type="VaultActivityRollbackConflict",
            message=str(exc),
            details={
                "vault_name": vault_name,
                "activity_id": activity_id,
                "code": exc.code,
            },
        ) from exc
    return VaultActivityRollbackResponse(
        success=True,
        source_activity_id=result.source_activity_id,
        rollback_activity_id=result.rollback_activity_id,
        vault_name=result.vault_name,
        restored_count=result.restored_count,
        deleted_count=result.deleted_count,
        message=(
            f"Rolled back {result.restored_count + result.deleted_count} path state(s)."
        ),
    )


def _vault_activity_rollback_preview_info(
    plan: ActivityRollbackPlan,
) -> VaultActivityRollbackPreviewResponse:
    return VaultActivityRollbackPreviewResponse(
        activity_id=plan.activity_id,
        activity_label=plan.activity_label,
        vault_name=plan.vault_name,
        can_rollback=plan.can_rollback,
        restore_count=plan.restore_count,
        delete_count=plan.delete_count,
        paths=[
            VaultActivityRollbackPathInfo(
                path=path.path,
                action=cast(Literal["restore", "delete"], path.action),
                expected_exists=path.expected_exists,
                expected_sha256=path.expected_sha256,
                restore_exists=path.restore_exists,
                restore_sha256=path.restore_sha256,
            )
            for path in plan.paths
        ],
        issues=[
            VaultActivityRollbackIssueInfo(
                code=issue.code,
                message=issue.message,
                path=issue.path,
            )
            for issue in plan.issues
        ],
    )


def cleanup_vault_state() -> VaultStateCleanupResponse:
    """Manually delete expired vault-state safety artifacts."""
    result = cleanup_expired_vault_state()
    return VaultStateCleanupResponse(
        success=True,
        expired_activity_rows_deleted=result.expired_activity_rows_deleted,
        expired_mutation_rows_deleted=result.expired_mutation_rows_deleted,
        expired_snapshot_rows_deleted=result.expired_snapshot_rows_deleted,
        snapshot_files_deleted=result.snapshot_files_deleted,
        snapshot_dirs_deleted=result.snapshot_dirs_deleted,
        message=(
            "Vault-state cleanup completed: "
            f"{result.expired_activity_rows_deleted} activity row(s), "
            f"{result.expired_mutation_rows_deleted} mutation row(s), "
            f"{result.expired_snapshot_rows_deleted} snapshot row(s), "
            f"{result.snapshot_files_deleted} snapshot file(s), "
            f"{result.snapshot_dirs_deleted} snapshot directory/directories deleted."
        ),
    )


def get_vault_snapshot_file(snapshot_id: int) -> SnapshotFileResponse:
    """Resolve a retained vault snapshot file for inline display."""
    if snapshot_id <= 0:
        raise APIException(
            status_code=400,
            error_type="InvalidSnapshotId",
            message="Snapshot id must be a positive integer.",
            details={"snapshot_id": snapshot_id},
        )
    snapshot = _vault_state_service().resolve_snapshot_file(snapshot_id)
    if snapshot is None:
        raise APIException(
            status_code=404,
            error_type="VaultSnapshotNotFound",
            message=f"Vault snapshot not found or no longer retained: {snapshot_id}",
            details={"snapshot_id": snapshot_id},
        )
    return SnapshotFileResponse(
        path=snapshot.path,
        filename=Path(snapshot.vault_path).name or f"snapshot-{snapshot_id}",
        media_type=mimetypes.guess_type(snapshot.vault_path)[0] or "text/plain",
    )


def _vault_state_service() -> VaultStateService:
    return VaultStateService()


def vault_activity_group_info(group: VaultActivityGroup) -> VaultActivityGroupInfo:
    """Project one durable activity group into its API representation."""
    chat_session = None
    if group.activity_kind == "chat" and group.chat_session_id:
        candidate = get_runtime_context().chat_session_access.get_session_by_id(
            group.chat_session_id
        )
        if candidate is not None and candidate.vault_name == group.vault_name:
            chat_session = candidate
    return VaultActivityGroupInfo(
        activity_id=group.activity_id,
        activity_kind=group.activity_kind,
        activity_label=group.activity_label,
        chat_session_id=group.chat_session_id,
        chat_session_title=(
            chat_session.title if chat_session else group.chat_session_title
        ),
        chat_session_created_at=(
            chat_session.created_at if chat_session else group.chat_session_created_at
        ),
        chat_session_last_activity_at=(
            chat_session.last_activity_at
            if chat_session
            else group.chat_session_last_activity_at
        ),
        status=group.status,
        rollback_status=group.rollback_status,
        task_id=group.task_id,
        task_kind=group.task_kind,
        task_source=group.task_source,
        task_scope=group.task_scope,
        task_label=group.task_label,
        goal_id=group.goal_id,
        step_id=group.step_id,
        vault_id=group.vault_id,
        vault_name=group.vault_name,
        mutation_count=group.mutation_count,
        operation_count=group.operation_count,
        first_mutation_at=group.first_mutation_at,
        last_mutation_at=group.last_mutation_at,
        expires_at=group.expires_at,
        mutations=[
            VaultMutationInfo(
                id=mutation.id,
                activity_id=mutation.activity_id,
                operation_id=mutation.operation_id,
                task_id=mutation.task_id,
                task_kind=mutation.task_kind,
                task_source=mutation.task_source,
                task_scope=mutation.task_scope,
                task_label=mutation.task_label,
                goal_id=mutation.goal_id,
                step_id=mutation.step_id,
                path=mutation.path,
                related_path=mutation.related_path,
                target_kind=cast(Literal["file", "directory"], mutation.target_kind),
                operation=mutation.operation,
                status=mutation.status,
                event_sequence=mutation.event_sequence,
                before_exists=mutation.before_exists,
                before_hash=mutation.before_hash,
                before_snapshot_id=mutation.before_snapshot_id,
                after_exists=mutation.after_exists,
                after_hash=mutation.after_hash,
                after_snapshot_id=mutation.after_snapshot_id,
                snapshot_ref=mutation.snapshot_ref,
                created_at=mutation.created_at,
                expires_at=mutation.expires_at,
                metadata=mutation.metadata,
            )
            for mutation in group.mutations
        ],
    )
