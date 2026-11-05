"""State-based planning and execution for explicit activity rollback."""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from core.logger import UnifiedLogger
from core.utils.hash import hash_file_bytes
from core.vault_state.activity import VaultActivityContext, use_vault_activity
from core.vault_state.favorites import remove_favorite_paths
from core.vault_state.file_mutations import (
    FileStateRestore,
    RecordedMutationResult,
    VaultMutationRejected,
    record_vault_directory_mutation,
    restore_vault_file_states,
    vault_directory_mutation_lock,
)
from core.vault_state.identity import resolve_or_create_vault_identity
from core.vault_state.models import FileSnapshot, VaultActivity, VaultMutation
from core.vault_state.pathing import resolve_vault_relative_path
from core.vault_state.service import VaultStateService

logger = UnifiedLogger(tag="vault-activity-rollback")
_ACTIVITY_ROLLBACK_LOCKS = tuple(threading.RLock() for _ in range(128))


@dataclass(frozen=True)
class ActivityRollbackIssue:
    """One reason an activity cannot currently be rolled back."""

    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class ActivityRollbackPath:
    """One exact path transition in an activity rollback plan."""

    path: str
    expected_exists: bool
    expected_sha256: str | None
    restore_exists: bool
    restore_sha256: str | None
    snapshot_id: int | None
    snapshot_path: Path | None
    target_kind: str = "file"

    @property
    def action(self) -> str:
        return "restore" if self.restore_exists else "delete"


@dataclass(frozen=True)
class ActivityRollbackPlan:
    """Current rollback availability and desired states for one activity."""

    activity_id: str
    activity_label: str
    activity_status: str
    vault_id: str
    vault_name: str
    can_rollback: bool
    paths: tuple[ActivityRollbackPath, ...]
    issues: tuple[ActivityRollbackIssue, ...]

    @property
    def restore_count(self) -> int:
        return sum(path.restore_exists for path in self.paths)

    @property
    def delete_count(self) -> int:
        return sum(not path.restore_exists for path in self.paths)


@dataclass(frozen=True)
class ActivityRollbackResult:
    """Outcome of one completed explicit activity rollback."""

    source_activity_id: str
    rollback_activity_id: str
    vault_name: str
    restored_count: int
    deleted_count: int


class ActivityRollbackUnavailable(Exception):
    """Raised when preflight rejects a requested activity rollback."""

    def __init__(self, plan: ActivityRollbackPlan) -> None:
        super().__init__("Activity rollback is not currently available.")
        self.plan = plan


def preview_activity_rollback(
    *,
    vault_path: str | Path,
    activity_id: str,
) -> ActivityRollbackPlan:
    """Build a state-based rollback plan without changing the filesystem."""
    vault_root = Path(vault_path).resolve()
    service = VaultStateService()
    now = datetime.now(UTC)
    activity, rows, snapshots = _load_rollback_records(
        service=service,
        vault_root=vault_root,
        activity_id=activity_id,
    )
    issues = _activity_rollback_issues(
        activity=activity,
        rows=rows,
        vault_root=vault_root,
        now=now,
    )
    paths: list[ActivityRollbackPath] = []
    for path, path_rows in _group_rows_by_path(rows):
        planned_path, path_issues = _plan_rollback_path(
            service=service,
            vault_root=vault_root,
            path=path,
            rows=path_rows,
            snapshots=snapshots,
            now=now,
        )
        paths.append(planned_path)
        issues.extend(path_issues)

    issues.extend(_directory_restore_parent_issues(vault_root, paths))
    planned_names = {item.path for item in paths}
    for item in paths:
        if item.target_kind != "directory" or item.restore_exists:
            continue
        directory = vault_root / item.path
        if directory.is_dir() and any(
            child.relative_to(vault_root).as_posix() not in planned_names
            for child in directory.rglob("*")
        ):
            issues.append(
                ActivityRollbackIssue(
                    code="state_conflict",
                    message=f"'{item.path}' contains newer items outside this activity.",
                    path=item.path,
                )
            )

    return ActivityRollbackPlan(
        activity_id=activity.activity_id,
        activity_label=activity.label,
        activity_status=activity.status,
        vault_id=activity.vault_id,
        vault_name=activity.vault_name,
        can_rollback=not issues,
        paths=tuple(paths),
        issues=tuple(_deduplicate_issues(issues)),
    )


def _directory_restore_parent_issues(
    vault_root: Path, paths: list[ActivityRollbackPath]
) -> list[ActivityRollbackIssue]:
    restoring = {
        path.path
        for path in paths
        if path.target_kind == "directory" and path.restore_exists
    }
    issues = []
    for path in paths:
        if path.path not in restoring:
            continue
        for parent in Path(path.path).parents:
            if parent == Path("."):
                break
            full_parent = vault_root / parent
            if full_parent.is_dir() and not full_parent.is_symlink():
                continue
            if (
                not full_parent.exists()
                and not full_parent.is_symlink()
                and parent.as_posix() in restoring
            ):
                continue
            issues.append(
                ActivityRollbackIssue(
                    code="parent_state_conflict",
                    message=f"Restore parent directory '{parent.as_posix()}' before rolling back '{path.path}'.",
                    path=path.path,
                )
            )
            break
    return issues


def _load_rollback_records(
    *,
    service: VaultStateService,
    vault_root: Path,
    activity_id: str,
) -> tuple[VaultActivity, tuple[VaultMutation, ...], dict[int, FileSnapshot]]:
    with service.SessionFactory() as session:
        activity = session.get(VaultActivity, activity_id)
        if activity is None or activity.vault_name != vault_root.name:
            raise LookupError(f"Vault activity not found: {activity_id}")
        rows = tuple(
            session.scalars(
                select(VaultMutation)
                .where(VaultMutation.activity_id == activity_id)
                .order_by(VaultMutation.created_at.asc(), VaultMutation.id.asc())
            )
        )
        snapshot_ids = tuple(
            row.before_snapshot_id for row in rows if row.before_snapshot_id is not None
        )
        snapshots = {
            snapshot.id: snapshot
            for snapshot in session.scalars(
                select(FileSnapshot).where(FileSnapshot.id.in_(snapshot_ids))
            )
        }
    return activity, rows, snapshots


def _activity_rollback_issues(
    *,
    activity: VaultActivity,
    rows: tuple[VaultMutation, ...],
    vault_root: Path,
    now: datetime,
) -> list[ActivityRollbackIssue]:
    issues: list[ActivityRollbackIssue] = []
    current_identity = resolve_or_create_vault_identity(vault_root)
    checks = (
        (
            current_identity.vault_id != activity.vault_id,
            "vault_identity_mismatch",
            "This activity belongs to an earlier vault with the same name.",
        ),
        (
            activity.status == "running",
            "activity_in_progress",
            "This activity is still in progress.",
        ),
        (
            _is_expired(activity.expires_at, now)
            or any(_is_expired(row.expires_at, now) for row in rows),
            "activity_expired",
            "This activity is outside its retained rollback window.",
        ),
        (
            activity.rollback_status == "completed",
            "already_rolled_back",
            "This activity has already been rolled back.",
        ),
        (
            not rows,
            "no_mutations",
            "This activity has no retained mutations to roll back.",
        ),
        (
            any(
                row.target_kind != "file" and not _recorded_directory_state(row)
                for row in rows
            ),
            "unsupported_directory_operation",
            "Directory operations do not retain enough state for full rollback.",
        ),
        (
            any(row.status != "completed" for row in rows),
            "incomplete_mutation",
            "This activity contains a mutation without a completed outcome.",
        ),
    )
    for failed, code, message in checks:
        if failed:
            issues.append(ActivityRollbackIssue(code=code, message=message))
    return issues


def _plan_rollback_path(
    *,
    service: VaultStateService,
    vault_root: Path,
    path: str,
    rows: tuple[VaultMutation, ...],
    snapshots: dict[int, FileSnapshot],
    now: datetime,
) -> tuple[ActivityRollbackPath, tuple[ActivityRollbackIssue, ...]]:
    first = rows[0]
    last = rows[-1]
    snapshot_path, snapshot_issue = _resolve_rollback_snapshot(
        service=service,
        path=path,
        mutation=first,
        snapshots=snapshots,
        now=now,
    )
    issues = [snapshot_issue] if snapshot_issue is not None else []
    full_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=path,
        markdown_only=False,
    )
    actual_exists = full_path.exists()
    actual_hash = (
        hash_file_bytes(full_path, length=None)
        if actual_exists and full_path.is_file()
        else None
    )
    wrong_kind = (
        (vault_root / path).is_symlink()
        or actual_exists
        and (
            full_path.is_dir()
            if first.target_kind == "file"
            else not full_path.is_dir()
        )
    )
    if (
        wrong_kind
        or actual_exists != bool(last.after_exists)
        or actual_hash != last.after_hash
    ):
        issues.append(
            ActivityRollbackIssue(
                code="state_conflict",
                message=f"'{path}' has changed since this activity completed.",
                path=path,
            )
        )
    return (
        ActivityRollbackPath(
            path=path,
            expected_exists=bool(last.after_exists),
            expected_sha256=last.after_hash,
            restore_exists=bool(first.before_exists),
            restore_sha256=first.before_hash,
            snapshot_id=first.before_snapshot_id,
            snapshot_path=snapshot_path,
            target_kind=first.target_kind,
        ),
        tuple(issues),
    )


def _resolve_rollback_snapshot(
    *,
    service: VaultStateService,
    path: str,
    mutation: VaultMutation,
    snapshots: dict[int, FileSnapshot],
    now: datetime,
) -> tuple[Path | None, ActivityRollbackIssue | None]:
    if _recorded_directory_state(mutation) or not mutation.before_exists:
        return None, None
    snapshot = snapshots.get(mutation.before_snapshot_id or 0)
    unavailable = ActivityRollbackIssue(
        code="snapshot_unavailable",
        message=f"The retained state for '{path}' is no longer available.",
        path=path,
    )
    if snapshot is None or _is_expired(snapshot.expires_at, now):
        return None, unavailable
    resolved = service.resolve_snapshot_file(snapshot.id)
    if resolved is None or not resolved.path.is_file():
        return None, unavailable
    if hash_file_bytes(resolved.path, length=None) != mutation.before_hash:
        return None, ActivityRollbackIssue(
            code="snapshot_invalid",
            message=f"The retained state for '{path}' failed integrity checking.",
            path=path,
        )
    return resolved.path, None


def execute_activity_rollback(
    *,
    vault_path: str | Path,
    activity_id: str,
    expected_states: Iterable[tuple[str, bool, str | None]],
) -> ActivityRollbackResult:
    """Execute one preflighted activity rollback as a new durable activity."""
    vault_root = Path(vault_path).resolve()
    lock_key = f"{vault_root}:{activity_id}"
    lock = _ACTIVITY_ROLLBACK_LOCKS[hash(lock_key) % len(_ACTIVITY_ROLLBACK_LOCKS)]
    with lock, vault_directory_mutation_lock(vault_root):
        return _execute_activity_rollback_locked(
            vault_root=vault_root,
            activity_id=activity_id,
            expected_states=expected_states,
        )


def _execute_activity_rollback_locked(
    *,
    vault_root: Path,
    activity_id: str,
    expected_states: Iterable[tuple[str, bool, str | None]],
) -> ActivityRollbackResult:
    """Execute rollback while exclusively owning the source activity lifecycle."""
    plan = preview_activity_rollback(vault_path=vault_root, activity_id=activity_id)
    expected_state_items = tuple(expected_states)
    expected_state_map = _expected_state_map(expected_state_items)
    if (
        not plan.can_rollback
        or len(expected_state_map) != len(expected_state_items)
        or expected_state_map
        != {
            path.path: (path.expected_exists, path.expected_sha256)
            for path in plan.paths
        }
    ):
        logger.info(
            "Vault activity rollback rejected",
            data={
                "event": "vault_activity_rollback_rejected",
                "status": "skipped",
                "source_activity_id": activity_id,
                "vault_id": plan.vault_id,
                "vault_name": plan.vault_name,
                "reason_codes": [issue.code for issue in plan.issues],
            },
        )
        raise ActivityRollbackUnavailable(plan)

    rollback_activity_id = f"activity_{uuid.uuid4().hex}"
    context = VaultActivityContext(
        activity_id=rollback_activity_id,
        kind="explorer",
        source="api",
        scope=None,
        label=f"Rollback: {plan.activity_label}",
    )
    service = VaultStateService()
    states = tuple(
        FileStateRestore(
            path=path.path,
            expected_exists=path.expected_exists,
            expected_sha256=path.expected_sha256,
            content_path=path.snapshot_path,
            content_sha256=path.restore_sha256,
        )
        for path in plan.paths
        if path.target_kind == "file"
    )
    service.ensure_activity(
        context=context,
        vault_path=vault_root,
        vault_name=plan.vault_name,
    )
    service.update_activity_metadata(
        activity_id=rollback_activity_id,
        metadata={"source_activity_id": activity_id},
    )
    logger.info(
        "Vault activity rollback started",
        data={
            "event": "vault_activity_rollback_started",
            "status": "running",
            "source_activity_id": activity_id,
            "rollback_activity_id": rollback_activity_id,
            "vault_id": plan.vault_id,
            "vault_name": plan.vault_name,
            "path_count": len(plan.paths),
        },
    )
    with use_vault_activity(context):
        try:
            _restore_activity_states(
                vault_root=vault_root, plan=plan, states=states, service=service
            )
        except Exception as exc:
            service.finish_activity(activity_id=rollback_activity_id, status="failed")
            logger.error(
                "Vault activity rollback failed",
                data={
                    "event": "vault_activity_rollback_failed",
                    "status": "failed",
                    "source_activity_id": activity_id,
                    "rollback_activity_id": rollback_activity_id,
                    "vault_id": plan.vault_id,
                    "vault_name": plan.vault_name,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            raise

    service.finish_activity(activity_id=rollback_activity_id, status="completed")
    service.set_activity_rollback_status(
        activity_id=activity_id,
        rollback_status="completed",
    )
    logger.add_sink("validation").info(
        "Vault activity rollback completed",
        data={
            "event": "vault_activity_rollback_completed",
            "status": "completed",
            "source_activity_id": activity_id,
            "rollback_activity_id": rollback_activity_id,
            "vault_id": plan.vault_id,
            "vault_name": plan.vault_name,
            "restored_count": plan.restore_count,
            "deleted_count": plan.delete_count,
        },
    )
    return ActivityRollbackResult(
        source_activity_id=activity_id,
        rollback_activity_id=rollback_activity_id,
        vault_name=plan.vault_name,
        restored_count=plan.restore_count,
        deleted_count=plan.delete_count,
    )


def _recorded_directory_state(row: VaultMutation) -> bool:
    return (
        row.target_kind == "directory"
        and json.loads(row.metadata_json or "{}").get("directory_state") is True
    )


def _record_directory_transition(
    vault_root: Path, path: ActivityRollbackPath, *, reverse: bool = False
) -> None:
    after = path.expected_exists if reverse else path.restore_exists
    before = path.restore_exists if reverse else path.expected_exists
    record_vault_directory_mutation(
        vault_path=vault_root,
        path=path.path,
        operation="rollback_activity",
        before_exists=before,
        after_exists=after,
        metadata={"directory_state": True},
    )


def _restore_activity_states(
    *,
    vault_root: Path,
    plan: ActivityRollbackPlan,
    states: tuple[FileStateRestore, ...],
    service: VaultStateService,
) -> None:
    created: list[ActivityRollbackPath] = []
    removed: list[ActivityRollbackPath] = []
    file_results: tuple[RecordedMutationResult, ...] = ()
    directories = sorted(
        (
            path
            for path in plan.paths
            if path.target_kind == "directory"
            and path.restore_exists != path.expected_exists
        ),
        key=lambda path: len(Path(path.path).parts),
    )
    try:
        for path in directories:
            if path.restore_exists:
                (vault_root / path.path).mkdir()
                created.append(path)
                _record_directory_transition(vault_root, path)
        if states:
            file_results = restore_vault_file_states(
                vault_path=vault_root,
                states=states,
                operation="rollback_activity",
                metadata={"source_activity_id": plan.activity_id},
            )
        for path in reversed(directories):
            if not path.restore_exists:
                (vault_root / path.path).rmdir()
                removed.append(path)
                _record_directory_transition(vault_root, path)
        if removed:
            remove_favorite_paths(vault_root, [path.path for path in removed])
        service.refresh_vault(vault_root, vault_name=plan.vault_name)
    except Exception as cause:
        try:
            for path in reversed(removed):
                (vault_root / path.path).mkdir()
                _record_directory_transition(vault_root, path, reverse=True)
            _compensate_activity_files(vault_root, file_results, service)
            for path in reversed(created):
                (vault_root / path.path).rmdir()
                _record_directory_transition(vault_root, path, reverse=True)
        except Exception as compensation_error:
            raise VaultMutationRejected(
                "mutation_state_uncertain",
                f"Rollback failed and compensation was incomplete: {compensation_error}",
            ) from cause
        raise


def _compensate_activity_files(
    vault_root: Path,
    results: tuple[RecordedMutationResult, ...],
    service: VaultStateService,
) -> None:
    states = []
    for result in results:
        snapshot = (
            service.resolve_snapshot_file(result.before_snapshot_id)
            if result.before_snapshot_id
            else None
        )
        if result.before_exists and snapshot is None:
            raise VaultMutationRejected(
                "snapshot_unavailable", f"Missing compensation snapshot: {result.path}"
            )
        states.append(
            FileStateRestore(
                path=result.path,
                expected_exists=result.after_exists,
                expected_sha256=result.after_hash,
                content_path=snapshot.path if snapshot else None,
                content_sha256=result.before_hash,
            )
        )
    if states:
        restore_vault_file_states(
            vault_path=vault_root,
            states=tuple(states),
            operation="rollback_compensation",
        )


def _group_rows_by_path(
    rows: Iterable[VaultMutation],
) -> tuple[tuple[str, tuple[VaultMutation, ...]], ...]:
    grouped: dict[str, list[VaultMutation]] = {}
    for row in rows:
        if row.target_kind == "file" or _recorded_directory_state(row):
            grouped.setdefault(row.path, []).append(row)
    return tuple((path, tuple(grouped[path])) for path in sorted(grouped))


def _expected_state_map(
    states: Iterable[tuple[str, bool, str | None]],
) -> dict[str, tuple[bool, str | None]]:
    return {path: (exists, sha256) for path, exists, sha256 in states}


def _is_expired(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    normalized = (
        value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    )
    return normalized < now


def _deduplicate_issues(
    issues: Iterable[ActivityRollbackIssue],
) -> tuple[ActivityRollbackIssue, ...]:
    unique: dict[tuple[str, str | None], ActivityRollbackIssue] = {}
    for issue in issues:
        unique.setdefault((issue.code, issue.path), issue)
    return tuple(unique.values())
