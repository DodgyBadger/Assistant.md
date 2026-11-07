"""Task-scoped vault mutation rollback."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from core.logger import UnifiedLogger
from core.runtime.execution_tasks import ExecutionTaskSnapshot
from core.runtime.state import get_runtime_context
from core.settings import get_task_rollback_enabled
from core.vault_state.file_mutations import (
    FileStateRestore,
    restore_file_states_atomically,
    vault_directory_mutation_lock,
)
from core.vault_state.identity import resolve_or_create_vault_identity
from core.vault_state.models import (
    SnapshotSet,
    VaultActivity,
    VaultMutation,
)
from core.vault_state.pathing import resolve_vault_relative_path
from core.vault_state.service import VaultStateService

logger = UnifiedLogger(tag="vault-rollback")

ROLLBACK_TRIGGER_STATUSES = frozenset({"failed", "cancelled", "timed_out"})
ROLLBACK_MUTATION_OPERATIONS = frozenset(
    {
        "write",
        "append",
        "edit_line",
        "replace_text",
        "truncate",
        "delete",
        "move",
        "copy",
        "mkdir",
    }
)


@dataclass(frozen=True)
class TaskRollbackResult:
    """Summary of a task rollback attempt."""

    task_id: str
    status: str
    skipped: bool
    reason: str | None
    mutation_rows_seen: int
    paths_restored: int
    paths_deleted: int
    vaults_refreshed: int
    rollback_status: str | None = None


@dataclass(frozen=True)
class TaskRollbackPathPlan:
    """One task-owned path restoration and its durable attribution."""

    activity: VaultActivity
    state: FileStateRestore
    snapshot_id: int | None
    snapshot_ref: str | None


def handle_task_terminal_for_rollback(snapshot: ExecutionTaskSnapshot) -> None:
    """TaskCoordinator terminal observer for rollback-triggering task states."""
    status = str(snapshot.status or "").strip().lower()
    if status not in ROLLBACK_TRIGGER_STATUSES:
        return
    try:
        rollback_task_file_mutations(
            task_id=snapshot.task_id,
            terminal_status=status,
            reason=snapshot.terminal_reason,
        )
    except Exception as exc:  # noqa: BLE001
        logger.add_sink("validation").error(
            "task_rollback_failed",
            data={
                "event": "task_rollback_failed",
                "task_id": snapshot.task_id,
                "terminal_status": status,
                "reason": snapshot.terminal_reason,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )


def rollback_task_file_mutations(
    *,
    task_id: str,
    terminal_status: str,
    reason: str | None = None,
) -> TaskRollbackResult:
    """Rollback retained file mutations for one terminal task when policy requires it."""
    status = str(terminal_status or "").strip().lower()
    policy_skip_reason = _task_rollback_policy_skip_reason(status)
    if policy_skip_reason is not None:
        return _skipped_result(
            task_id=task_id,
            status=status,
            reason=policy_skip_reason,
        )

    service = VaultStateService()
    now = datetime.now(UTC)
    paths_restored = 0
    paths_deleted = 0
    vaults_refreshed = 0

    with service.SessionFactory() as session:
        all_rows = (
            session.query(VaultMutation)
            .join(
                VaultActivity,
                VaultActivity.activity_id == VaultMutation.activity_id,
            )
            .filter(VaultActivity.task_id == task_id)
            .filter(VaultMutation.operation.in_(ROLLBACK_MUTATION_OPERATIONS))
            .order_by(VaultMutation.id.asc())
            .all()
        )
        activities = {
            activity.activity_id: activity
            for activity in session.query(VaultActivity)
            .filter(VaultActivity.task_id == task_id)
            .all()
        }
        active_rows = [
            row
            for row in all_rows
            if activities[row.activity_id].rollback_status != "completed"
        ]
        if all_rows and not active_rows:
            result = _skipped_result(
                task_id=task_id,
                status=status,
                reason="already_rolled_back",
                mutation_rows_seen=len(all_rows),
            )
            logger.set_sinks(["validation"]).info(
                "task_rollback_skipped",
                data={
                    "event": "task_rollback_skipped",
                    "task_id": task_id,
                    "terminal_status": status,
                    "reason": result.reason,
                    "mutation_rows_seen": len(all_rows),
                },
            )
            return result
        rows = [row for row in active_rows if row.target_kind == "file"]
        directory_deletions = [
            row for row in active_rows if _recorded_directory_deletion(row)
        ]
        nonrollbackable_rows = [
            row
            for row in active_rows
            if row.target_kind != "file" and not _recorded_directory_deletion(row)
        ]
        if not rows and not directory_deletions:
            if nonrollbackable_rows:
                service.finish_task_activities(
                    task_id=task_id,
                    status=status,
                    rollback_status="not_available",
                )
            return _skipped_result(
                task_id=task_id,
                status=status,
                reason=(
                    "no_rollbackable_mutations"
                    if nonrollbackable_rows
                    else "no_mutations"
                ),
                mutation_rows_seen=len(all_rows),
            )
        snapshot_sets = (
            session.query(SnapshotSet)
            .filter(
                SnapshotSet.task_id == task_id,
                SnapshotSet.purpose == "rollback",
            )
            .all()
        )
        if snapshot_sets and all(
            snapshot.status == "rolled_back" for snapshot in snapshot_sets
        ):
            result = _skipped_result(
                task_id=task_id,
                status=status,
                reason="already_rolled_back",
                mutation_rows_seen=len(all_rows),
            )
            logger.add_sink("validation").info(
                "task_rollback_skipped",
                data={
                    "event": "task_rollback_skipped",
                    "task_id": task_id,
                    "terminal_status": status,
                    "reason": result.reason,
                    "mutation_rows_seen": result.mutation_rows_seen,
                },
            )
            return result

        logger.add_sink("validation").info(
            "task_rollback_started",
            data={
                "event": "task_rollback_started",
                "task_id": task_id,
                "terminal_status": status,
                "reason": reason,
                "mutation_rows_seen": len(all_rows),
            },
        )

        plans = _task_rollback_plans(
            service=service,
            rows=rows,
            activities=activities,
            task_id=task_id,
        )
        directory_plans: dict[str, list[VaultMutation]] = {}
        initial_directory_states: dict[tuple[str, str], bool] = {}
        for row in active_rows:
            if row.target_kind == "directory":
                initial_directory_states.setdefault(
                    (activities[row.activity_id].vault_name, row.path),
                    bool(row.before_exists),
                )
        for row in directory_deletions:
            vault_name = activities[row.activity_id].vault_name
            if initial_directory_states[(vault_name, row.path)]:
                directory_plans.setdefault(vault_name, []).append(row)
                plans.setdefault(vault_name, [])
        for vault_name, path_plans in sorted(plans.items()):
            vault_root = _vault_root(vault_name)
            current_identity = resolve_or_create_vault_identity(vault_root)
            expected_vault_id = (
                path_plans[0].activity.vault_id
                if path_plans
                else activities[directory_plans[vault_name][0].activity_id].vault_id
            )
            if current_identity.vault_id != expected_vault_id:
                raise RuntimeError(
                    f"Cannot rollback task mutations for an earlier vault named '{vault_name}'"
                )
            with vault_directory_mutation_lock(vault_root):
                directories = {
                    resolve_vault_relative_path(vault_path=vault_root, path=row.path)
                    for row in directory_plans.get(vault_name, [])
                }
                for directory in directories:
                    if directory.exists() and not directory.is_dir():
                        raise RuntimeError(
                            "A deleted directory was replaced by a file; task rollback cannot restore it."
                        )
                transitions = (
                    restore_file_states_atomically(
                        vault_path=vault_root,
                        states=tuple(plan.state for plan in path_plans),
                    )
                    if path_plans
                    else ()
                )
                for directory in sorted(directories, key=lambda path: len(path.parts)):
                    existed = directory.is_dir()
                    directory.mkdir(parents=True, exist_ok=True)
                    paths_restored += int(not existed)
                if directories:
                    service.refresh_vault(vault_root, vault_name=vault_name)
            vaults_refreshed += 1
            for plan, transition in zip(path_plans, transitions, strict=True):
                if plan.state.restore_exists:
                    paths_restored += 1
                    event_name = "task_rollback_file_restored"
                else:
                    paths_deleted += int(transition.before_exists)
                    event_name = "task_rollback_file_deleted"
                logger.add_sink("validation").info(
                    event_name,
                    data={
                        "event": event_name,
                        "task_id": task_id,
                        "vault_id": plan.activity.vault_id,
                        "vault_name": plan.activity.vault_name,
                        "path": plan.state.path,
                        "file_snapshot_id": plan.snapshot_id,
                        "snapshot_ref": plan.snapshot_ref,
                    },
                )

        for snapshot in snapshot_sets:
            snapshot.status = "rolled_back"
            snapshot.rolled_back_at = now
        session.commit()

    result = TaskRollbackResult(
        task_id=task_id,
        status=status,
        skipped=False,
        reason=reason,
        mutation_rows_seen=len(all_rows),
        paths_restored=paths_restored,
        paths_deleted=paths_deleted,
        vaults_refreshed=vaults_refreshed,
        rollback_status=("partial" if nonrollbackable_rows else "completed"),
    )
    rollback_status = "partial" if nonrollbackable_rows else "completed"
    service.finish_task_activities(
        task_id=task_id,
        status=status if nonrollbackable_rows else "rolled_back",
        rollback_status=rollback_status,
    )
    logger.add_sink("validation").info(
        "task_rollback_completed",
        data={
            "event": "task_rollback_completed",
            "task_id": task_id,
            "terminal_status": status,
            "reason": reason,
            "mutation_rows_seen": result.mutation_rows_seen,
            "paths_restored": result.paths_restored,
            "paths_deleted": result.paths_deleted,
            "vaults_refreshed": result.vaults_refreshed,
            "rollback_status": rollback_status,
            "nonrollbackable_mutation_rows": len(nonrollbackable_rows),
        },
    )
    return result


def _recorded_directory_deletion(row: VaultMutation) -> bool:
    return (
        row.target_kind == "directory"
        and row.operation == "delete"
        and bool(row.before_exists)
        and not row.after_exists
        and json.loads(row.metadata_json or "{}").get("directory_state") is True
    )


def _task_rollback_policy_skip_reason(status: str) -> str | None:
    if status not in ROLLBACK_TRIGGER_STATUSES:
        return "status_not_rollbackable"
    if not get_task_rollback_enabled():
        return "task_rollback_disabled"
    return None


def _task_rollback_plans(
    *,
    service: VaultStateService,
    rows: list[VaultMutation],
    activities: dict[str, VaultActivity],
    task_id: str,
) -> dict[str, list[TaskRollbackPathPlan]]:
    grouped: dict[tuple[str, str], list[VaultMutation]] = {}
    for row in rows:
        activity = activities[row.activity_id]
        grouped.setdefault((activity.vault_name, row.path), []).append(row)

    plans: dict[str, list[TaskRollbackPathPlan]] = {}
    for (vault_name, _path), group in grouped.items():
        first = group[0]
        last = group[-1]
        activity = activities[first.activity_id]
        content_path = None
        if first.before_exists:
            if first.before_snapshot_id is None:
                raise RuntimeError(
                    f"Cannot rollback '{first.path}' for task '{task_id}': missing file snapshot"
                )
            snapshot = service.resolve_snapshot_file(first.before_snapshot_id)
            if snapshot is None:
                raise RuntimeError(
                    f"Cannot rollback '{first.path}' for task '{task_id}': snapshot file missing"
                )
            content_path = snapshot.path
        plans.setdefault(vault_name, []).append(
            TaskRollbackPathPlan(
                activity=activity,
                state=FileStateRestore(
                    path=first.path,
                    expected_exists=bool(last.after_exists),
                    expected_sha256=last.after_hash,
                    content_path=content_path,
                    content_sha256=first.before_hash,
                ),
                snapshot_id=first.before_snapshot_id,
                snapshot_ref=first.snapshot_ref,
            )
        )
    for path_plans in plans.values():
        path_plans.sort(key=lambda plan: plan.state.path)
    return plans


def _vault_root(vault_name: str) -> Path:
    runtime = get_runtime_context()
    vault_info = runtime.workflow_loader.get_vault_info()
    vault_path = (vault_info.get(vault_name) or {}).get("path")
    if not vault_path:
        raise RuntimeError(
            f"Cannot rollback task mutation: vault not found: {vault_name}"
        )
    return Path(vault_path)


def _skipped_result(
    *,
    task_id: str,
    status: str,
    reason: str,
    mutation_rows_seen: int = 0,
) -> TaskRollbackResult:
    return TaskRollbackResult(
        task_id=task_id,
        status=status,
        skipped=True,
        reason=reason,
        mutation_rows_seen=mutation_rows_seen,
        paths_restored=0,
        paths_deleted=0,
        vaults_refreshed=0,
        rollback_status=None,
    )
