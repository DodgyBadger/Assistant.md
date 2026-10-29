"""Vault-state manifest refresh and change-feed service."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from sqlalchemy import or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from core.authoring.template_discovery import discover_vaults
from core.constants import ASSISTANTMD_ROOT_DIR, AUTHORING_DIR
from core.database import (
    create_engine_from_system_db,
    create_session_factory,
    get_system_database_path,
)
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import chat_session_scope
from core.settings import (
    get_debug_enabled,
    get_vault_state_enabled,
    get_vault_state_excluded_patterns,
)
from core.utils.hash import hash_file_bytes
from core.vault_state.activity import VaultActivityContext
from core.vault_state.identity import resolve_or_create_vault_identity
from core.vault_state.models import (
    FileSnapshot,
    SnapshotSet,
    VaultActivity,
    VaultFile,
    VaultFileEvent,
    VaultMutation,
    VaultRecord,
)
from core.vault_state.patterns import ExcludedPathMatcher
from core.vault_state.schema import ensure_vault_state_schema
from core.vault_state.snapshots import compute_task_mutation_expiration

logger = UnifiedLogger(tag="vault-state")


@dataclass(frozen=True)
class VaultStateRefreshResult:
    """Summary of one vault-state refresh."""

    vault_id: str
    vault_name: str
    files_seen: int
    files_created: int
    files_changed: int
    files_deleted: int
    files_unchanged: int
    files_excluded: int
    latest_sequence: int | None
    changed_paths: tuple[str, ...] = field(default_factory=tuple)
    deleted_paths: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class VaultMutationItem:
    """One recorded path mutation from a durable vault activity."""

    id: int
    activity_id: str
    operation_id: str
    task_id: str | None
    task_kind: str | None
    task_source: str | None
    task_scope: str | None
    task_label: str | None
    goal_id: str | None
    step_id: str | None
    path: str
    related_path: str | None
    target_kind: str
    operation: str
    status: str
    event_sequence: int | None
    before_exists: bool
    before_hash: str | None
    before_snapshot_id: int | None
    after_exists: bool
    after_hash: str | None
    after_snapshot_id: int | None
    snapshot_ref: str | None
    created_at: datetime
    expires_at: datetime | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VaultActivityGroup:
    """Recorded vault mutations grouped by attributed activity."""

    activity_id: str
    activity_kind: str
    activity_label: str
    chat_session_id: str | None
    chat_session_title: str | None
    chat_session_created_at: str | None
    chat_session_last_activity_at: str | None
    status: str
    rollback_status: str | None
    task_id: str | None
    task_kind: str | None
    task_source: str | None
    task_scope: str | None
    task_label: str | None
    goal_id: str | None
    step_id: str | None
    vault_id: str
    vault_name: str
    mutation_count: int
    operation_count: int
    first_mutation_at: datetime
    last_mutation_at: datetime
    expires_at: datetime | None
    mutations: tuple[VaultMutationItem, ...]


@dataclass(frozen=True)
class VaultSnapshotFile:
    """Resolved retained snapshot file safe for API serving."""

    snapshot_id: int
    path: Path
    vault_path: str
    content_hash: str | None


@dataclass(frozen=True)
class VaultFileRevision:
    """One retained pre-mutation state for a vault path."""

    snapshot_id: int
    activity_id: str
    activity_kind: str
    activity_source: str
    activity_label: str
    task_id: str | None
    path: str
    operation: str
    exists: bool
    content_hash: str | None
    snapshot_available: bool
    created_at: datetime
    expires_at: datetime | None


class VaultStateService:
    """Maintain a rebuildable vault manifest and monotonic change feed."""

    def __init__(self) -> None:
        ensure_vault_state_schema()
        self.engine = create_engine_from_system_db("vault_state")
        self.SessionFactory = create_session_factory(self.engine)

    def refresh_vault(
        self,
        vault_path: str | Path,
        *,
        vault_name: str | None = None,
        log_activity: bool = True,
    ) -> VaultStateRefreshResult:
        """Refresh the manifest for one vault path."""
        if not get_vault_state_enabled():
            raise RuntimeError("vault_state_enabled is false")

        root = Path(vault_path).resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Vault path is not a directory: {root}")

        resolved_name = vault_name or root.name
        identity = resolve_or_create_vault_identity(root)
        matcher = ExcludedPathMatcher.from_patterns(get_vault_state_excluded_patterns())
        now = datetime.now(UTC)

        start_logger = (
            logger.add_sink("validation")
            if log_activity
            else logger.set_sinks(["validation"])
        )
        start_logger.info(
            "vault_state_refresh_started",
            data={
                "event": "vault_state_refresh_started",
                "vault_id": identity.vault_id,
                "vault_name": resolved_name,
            },
        )

        seen_paths: set[str] = set()
        changed_paths: list[str] = []
        deleted_paths: list[str] = []
        files_created = 0
        files_changed = 0
        files_unchanged = 0
        files_excluded = 0
        latest_sequence: int | None = None

        with self.SessionFactory() as session:
            self._register_vault(
                session,
                vault_id=identity.vault_id,
                vault_name=resolved_name,
                now=now,
            )

            existing_rows = {
                row.path: row
                for row in session.scalars(
                    select(VaultFile).where(VaultFile.vault_id == identity.vault_id)
                )
            }

            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                relative_path = self._relative_path(root, path)
                if matcher.matches(relative_path):
                    files_excluded += 1
                    continue
                seen_paths.add(relative_path)
                stat = path.stat()
                artifact_class = self._classify(relative_path)
                existing = existing_rows.get(relative_path)
                needs_hash = (
                    existing is None
                    or existing.deleted_at is not None
                    or existing.size != stat.st_size
                    or existing.mtime_ns != stat.st_mtime_ns
                    or existing.artifact_class != artifact_class
                )
                if not needs_hash:
                    assert existing is not None
                    existing.last_seen_at = now
                    existing.vault_name = resolved_name
                    files_unchanged += 1
                    continue

                content_hash = hash_file_bytes(path, length=None)
                event_type = self._event_type(existing, content_hash, artifact_class)
                if existing is None:
                    event = self._append_event(
                        session,
                        vault_id=identity.vault_id,
                        vault_name=resolved_name,
                        path=relative_path,
                        event_type=event_type,
                        content_hash=content_hash,
                        artifact_class=artifact_class,
                        now=now,
                        metadata={},
                    )
                    latest_sequence = event.sequence
                    session.flush()
                    row = VaultFile(
                        vault_id=identity.vault_id,
                        vault_name=resolved_name,
                        path=relative_path,
                        artifact_class=artifact_class,
                        size=stat.st_size,
                        mtime_ns=stat.st_mtime_ns,
                        content_hash=content_hash,
                        kind="file",
                        change_sequence=event.sequence,
                        first_seen_at=now,
                        last_seen_at=now,
                        changed_at=now,
                        deleted_at=None,
                    )
                    session.add(row)
                    files_created += 1
                elif (
                    existing.content_hash != content_hash
                    or existing.deleted_at is not None
                    or existing.artifact_class != artifact_class
                ):
                    was_deleted = existing.deleted_at is not None
                    event = self._append_event(
                        session,
                        vault_id=identity.vault_id,
                        vault_name=resolved_name,
                        path=relative_path,
                        event_type=event_type,
                        content_hash=content_hash,
                        artifact_class=artifact_class,
                        now=now,
                        metadata={"previous_hash": existing.content_hash},
                    )
                    latest_sequence = event.sequence
                    session.flush()
                    existing.vault_name = resolved_name
                    existing.artifact_class = artifact_class
                    existing.size = stat.st_size
                    existing.mtime_ns = stat.st_mtime_ns
                    existing.content_hash = content_hash
                    existing.change_sequence = event.sequence
                    existing.last_seen_at = now
                    existing.changed_at = now
                    existing.deleted_at = None
                    if was_deleted:
                        files_created += 1
                    else:
                        files_changed += 1
                else:
                    existing.vault_name = resolved_name
                    existing.artifact_class = artifact_class
                    existing.size = stat.st_size
                    existing.mtime_ns = stat.st_mtime_ns
                    existing.last_seen_at = now
                    existing.deleted_at = None
                    files_unchanged += 1
                    continue

                changed_paths.append(relative_path)
                self._log_file_event(
                    event_type=event_type,
                    vault_id=identity.vault_id,
                    vault_name=resolved_name,
                    path=relative_path,
                    content_hash=content_hash,
                    artifact_class=artifact_class,
                    sequence=latest_sequence,
                )

            files_deleted = 0
            for relative_path, existing in sorted(existing_rows.items()):
                if relative_path in seen_paths or existing.deleted_at is not None:
                    continue
                event = self._append_event(
                    session,
                    vault_id=identity.vault_id,
                    vault_name=resolved_name,
                    path=relative_path,
                    event_type="deleted",
                    content_hash=existing.content_hash,
                    artifact_class=existing.artifact_class,
                    now=now,
                    metadata={},
                )
                session.flush()
                latest_sequence = event.sequence
                existing.vault_name = resolved_name
                existing.last_seen_at = now
                existing.change_sequence = event.sequence
                existing.deleted_at = now
                files_deleted += 1
                deleted_paths.append(relative_path)
                self._log_file_event(
                    event_type="deleted",
                    vault_id=identity.vault_id,
                    vault_name=resolved_name,
                    path=relative_path,
                    content_hash=existing.content_hash,
                    artifact_class=existing.artifact_class,
                    sequence=event.sequence,
                )

            session.commit()

        result = VaultStateRefreshResult(
            vault_id=identity.vault_id,
            vault_name=resolved_name,
            files_seen=len(seen_paths),
            files_created=files_created,
            files_changed=files_changed,
            files_deleted=files_deleted,
            files_unchanged=files_unchanged,
            files_excluded=files_excluded,
            latest_sequence=latest_sequence,
            changed_paths=tuple(changed_paths),
            deleted_paths=tuple(deleted_paths),
        )
        completion_logger = (
            logger.add_sink("validation")
            if log_activity
            else logger.set_sinks(["validation"])
        )
        completion_logger.info(
            "vault_state_refresh_completed",
            data={
                "event": "vault_state_refresh_completed",
                "vault_id": result.vault_id,
                "vault_name": result.vault_name,
                "files_seen": result.files_seen,
                "files_created": result.files_created,
                "files_changed": result.files_changed,
                "files_deleted": result.files_deleted,
                "files_unchanged": result.files_unchanged,
                "files_excluded": result.files_excluded,
                "latest_sequence": result.latest_sequence,
            },
        )
        return result

    def changes_since(
        self,
        sequence: int,
        *,
        vault_id: str | None = None,
        limit: int | None = None,
    ) -> list[VaultFileEvent]:
        """Return change-feed events after a sequence cursor."""
        with self.SessionFactory() as session:
            stmt = select(VaultFileEvent).where(VaultFileEvent.sequence > sequence)
            if vault_id:
                stmt = stmt.where(VaultFileEvent.vault_id == vault_id)
            stmt = stmt.order_by(VaultFileEvent.sequence.asc())
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.scalars(stmt))

    def ensure_activity(
        self,
        *,
        context: VaultActivityContext,
        vault_path: str | Path,
        vault_name: str | None = None,
        created_at: datetime | None = None,
    ) -> VaultActivity:
        """Create or return one durable activity header."""
        root = Path(vault_path).resolve()
        identity = resolve_or_create_vault_identity(root)
        now = created_at or datetime.now(UTC)
        with self.SessionFactory() as session:
            activity = session.get(VaultActivity, context.activity_id)
            if activity is None:
                activity = VaultActivity(
                    activity_id=context.activity_id,
                    vault_id=identity.vault_id,
                    vault_name=vault_name or root.name,
                    kind=context.kind,
                    source=context.source,
                    scope=context.scope,
                    label=context.label,
                    task_id=context.task_id,
                    goal_id=context.goal_id,
                    step_id=context.step_id,
                    status="running",
                    rollback_status=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                    expires_at=compute_task_mutation_expiration(now),
                    metadata_json=None,
                )
                session.add(activity)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    activity = session.get(VaultActivity, context.activity_id)
                    if activity is None:
                        raise
                else:
                    session.refresh(activity)
            session.expunge(activity)
            return cast(VaultActivity, activity)

    def finish_activity(
        self,
        *,
        activity_id: str,
        status: str,
        rollback_status: str | None = None,
    ) -> None:
        """Persist one activity outcome when the activity exists."""
        now = datetime.now(UTC)
        event_data: dict[str, Any] | None = None
        with self.SessionFactory() as session:
            activity = session.get(VaultActivity, activity_id)
            if activity is None:
                return
            if activity.status == "rolled_back" and status != "rolled_back":
                return
            effective_rollback_status = (
                rollback_status
                if rollback_status is not None
                else activity.rollback_status
            )
            if (
                activity.status == status
                and activity.rollback_status == effective_rollback_status
                and activity.completed_at is not None
            ):
                return
            activity.status = status
            activity.rollback_status = effective_rollback_status
            activity.updated_at = now
            activity.completed_at = now
            event_data = {
                "activity_id": activity_id,
                "vault_id": activity.vault_id,
                "vault_name": activity.vault_name,
                "kind": activity.kind,
                "source": activity.source,
                "task_id": activity.task_id,
                "status": status,
                "rollback_status": effective_rollback_status,
            }
            session.commit()
        if event_data is None:
            return
        logger.add_sink("validation").info(
            "vault_activity_completed",
            data={
                "event": "vault_activity_completed",
                **event_data,
            },
        )

    def update_activity_metadata(
        self,
        *,
        activity_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Merge bounded provenance metadata into one existing activity."""
        with self.SessionFactory() as session:
            activity = session.get(VaultActivity, activity_id)
            if activity is None:
                raise RuntimeError(f"Vault activity not found: {activity_id}")
            current: dict[str, Any] = {}
            if activity.metadata_json:
                parsed = json.loads(activity.metadata_json)
                if isinstance(parsed, dict):
                    current = parsed
            current.update(metadata)
            activity.metadata_json = json.dumps(current, sort_keys=True)
            activity.updated_at = datetime.now(UTC)
            session.commit()

    def set_activity_rollback_status(
        self,
        *,
        activity_id: str,
        rollback_status: str,
    ) -> None:
        """Update only the later rollback outcome of a completed activity."""
        with self.SessionFactory() as session:
            activity = session.get(VaultActivity, activity_id)
            if activity is None:
                raise RuntimeError(f"Vault activity not found: {activity_id}")
            activity.rollback_status = rollback_status
            activity.updated_at = datetime.now(UTC)
            session.commit()

    def finish_task_activities(
        self,
        *,
        task_id: str,
        status: str,
        rollback_status: str | None = None,
    ) -> int:
        """Persist a terminal outcome for all vault activities owned by one task."""
        with self.SessionFactory() as session:
            activity_ids = list(
                session.scalars(
                    select(VaultActivity.activity_id).where(
                        VaultActivity.task_id == task_id
                    )
                )
            )
        for activity_id in activity_ids:
            self.finish_activity(
                activity_id=activity_id,
                status=status,
                rollback_status=rollback_status,
            )
        return len(activity_ids)

    def list_activities(
        self,
        *,
        vault_name: str,
        limit: int = 50,
        task_id: str | None = None,
        include_expired: bool = False,
        operation: str | None = None,
        goal_id: str | None = None,
        step_id: str | None = None,
    ) -> list[VaultActivityGroup]:
        """Return recent attributed vault activities for one vault."""
        now = datetime.now(UTC)
        group_limit = min(max(limit, 1), 100)
        with self.SessionFactory() as session:
            stmt = select(VaultActivity).where(VaultActivity.vault_name == vault_name)
            if task_id:
                stmt = stmt.where(VaultActivity.task_id == task_id)
            if operation:
                stmt = (
                    stmt.join(
                        VaultMutation,
                        VaultMutation.activity_id == VaultActivity.activity_id,
                    )
                    .where(VaultMutation.operation == operation)
                    .distinct()
                )
            if goal_id:
                stmt = stmt.where(VaultActivity.goal_id == goal_id)
            if step_id:
                stmt = stmt.where(VaultActivity.step_id == step_id)
            if not include_expired:
                stmt = stmt.where(
                    or_(
                        VaultActivity.expires_at.is_(None),
                        VaultActivity.expires_at >= now,
                    )
                )
            stmt = stmt.order_by(
                VaultActivity.updated_at.desc(),
                VaultActivity.activity_id.desc(),
            )
            activities = list(session.scalars(stmt.limit(group_limit)))

            groups: list[VaultActivityGroup] = []
            for activity in activities:
                rows = list(
                    session.scalars(
                        select(VaultMutation)
                        .where(VaultMutation.activity_id == activity.activity_id)
                        .order_by(
                            VaultMutation.created_at.asc(), VaultMutation.id.asc()
                        )
                    )
                )
                first_at = rows[0].created_at if rows else activity.created_at
                last_at = rows[-1].created_at if rows else activity.updated_at
                expires_values = [
                    value
                    for value in [
                        activity.expires_at,
                        *(row.expires_at for row in rows),
                    ]
                    if value is not None
                ]
                groups.append(
                    self._activity_group(
                        activity, rows, first_at, last_at, expires_values
                    )
                )
        return groups

    @staticmethod
    def _activity_group(
        activity: VaultActivity,
        rows: list[VaultMutation],
        first_at: datetime,
        last_at: datetime,
        expires_values: list[datetime],
    ) -> VaultActivityGroup:
        return VaultActivityGroup(
            activity_id=activity.activity_id,
            activity_kind=activity.kind,
            activity_label=activity.label,
            chat_session_id=_chat_session_id(activity.kind, activity.scope),
            chat_session_title=None,
            chat_session_created_at=None,
            chat_session_last_activity_at=None,
            status=activity.status,
            rollback_status=activity.rollback_status,
            task_id=activity.task_id,
            task_kind=activity.kind if activity.task_id else None,
            task_source=activity.source if activity.task_id else None,
            task_scope=activity.scope if activity.task_id else None,
            task_label=activity.label if activity.task_id else None,
            goal_id=activity.goal_id,
            step_id=activity.step_id,
            vault_id=activity.vault_id,
            vault_name=activity.vault_name,
            mutation_count=len(rows),
            operation_count=len({row.operation_id for row in rows}),
            first_mutation_at=first_at,
            last_mutation_at=last_at,
            expires_at=min(expires_values) if expires_values else None,
            mutations=tuple(_mutation_item(activity, row) for row in rows),
        )

    def list_chat_session_mutations(
        self,
        *,
        vault_name: str,
        session_id: str,
        include_expired: bool = False,
    ) -> tuple[VaultMutationItem, ...]:
        """Return file mutations recorded for one chat session."""
        now = datetime.now(UTC)
        scope = chat_session_scope(session_id)
        with self.SessionFactory() as session:
            stmt = (
                select(VaultMutation, VaultActivity)
                .join(
                    VaultActivity,
                    VaultActivity.activity_id == VaultMutation.activity_id,
                )
                .where(
                    VaultActivity.vault_name == vault_name,
                    VaultActivity.kind == "chat",
                    VaultActivity.scope == scope,
                )
            )
            if not include_expired:
                stmt = stmt.where(
                    or_(
                        VaultMutation.expires_at.is_(None),
                        VaultMutation.expires_at >= now,
                    )
                )
            stmt = stmt.order_by(VaultMutation.created_at.asc(), VaultMutation.id.asc())
            rows = list(session.execute(stmt))

        return tuple(_mutation_item(activity, mutation) for mutation, activity in rows)

    def list_file_revisions(
        self,
        *,
        vault_name: str,
        path: str,
        limit: int = 50,
        include_expired: bool = False,
    ) -> tuple[VaultFileRevision, ...]:
        """Return retained pre-mutation states for one exact vault path."""
        now = datetime.now(UTC)
        row_limit = min(max(limit, 1), 100)
        with self.SessionFactory() as session:
            stmt = (
                select(VaultMutation, VaultActivity, FileSnapshot)
                .join(
                    VaultActivity,
                    VaultActivity.activity_id == VaultMutation.activity_id,
                )
                .join(
                    FileSnapshot,
                    FileSnapshot.id == VaultMutation.before_snapshot_id,
                )
                .where(
                    VaultActivity.vault_name == vault_name,
                    VaultMutation.path == path,
                    VaultMutation.target_kind == "file",
                )
            )
            if not include_expired:
                stmt = stmt.where(
                    or_(
                        FileSnapshot.expires_at.is_(None),
                        FileSnapshot.expires_at >= now,
                    )
                )
            stmt = stmt.order_by(
                VaultMutation.created_at.desc(),
                VaultMutation.id.desc(),
            ).limit(row_limit)
            rows = list(session.execute(stmt))

        return tuple(
            _file_revision_item(mutation, activity, snapshot)
            for mutation, activity, snapshot in rows
        )

    def get_file_revision(
        self,
        *,
        vault_name: str,
        snapshot_id: int,
    ) -> VaultFileRevision | None:
        """Return one retained mutation revision scoped to a vault."""
        now = datetime.now(UTC)
        with self.SessionFactory() as session:
            row = session.execute(
                select(VaultMutation, VaultActivity, FileSnapshot)
                .join(
                    VaultActivity,
                    VaultActivity.activity_id == VaultMutation.activity_id,
                )
                .join(
                    FileSnapshot,
                    FileSnapshot.id == VaultMutation.before_snapshot_id,
                )
                .where(
                    VaultActivity.vault_name == vault_name,
                    VaultMutation.target_kind == "file",
                    FileSnapshot.id == snapshot_id,
                    or_(
                        FileSnapshot.expires_at.is_(None),
                        FileSnapshot.expires_at >= now,
                    ),
                )
                .order_by(VaultMutation.created_at.desc(), VaultMutation.id.desc())
                .limit(1)
            ).first()
        if row is None:
            return None
        mutation, activity, snapshot = row
        return _file_revision_item(mutation, activity, snapshot)

    def resolve_snapshot_file(self, snapshot_id: int) -> VaultSnapshotFile | None:
        """Resolve one retained file snapshot to an on-disk path under the managed snapshot root."""
        with self.SessionFactory() as session:
            file_snapshot = session.get(FileSnapshot, snapshot_id)
            if file_snapshot is None:
                return None
            snapshot_set = session.get(SnapshotSet, file_snapshot.snapshot_set_id)
            if snapshot_set is None or not file_snapshot.snapshot_ref:
                return None

            snapshot_path = (
                Path(snapshot_set.snapshot_root) / file_snapshot.snapshot_ref
            ).resolve()
            snapshot_base = _snapshot_base_root().resolve()
            try:
                snapshot_path.relative_to(snapshot_base)
            except ValueError:
                logger.warning(
                    "Refusing to serve snapshot outside managed root",
                    data={
                        "event": "snapshot_serve_rejected",
                        "snapshot_id": snapshot_id,
                        "snapshot_path": str(snapshot_path),
                        "snapshot_base": str(snapshot_base),
                    },
                )
                return None

            if not snapshot_path.is_file():
                return None

            return VaultSnapshotFile(
                snapshot_id=snapshot_id,
                path=snapshot_path,
                vault_path=file_snapshot.path,
                content_hash=file_snapshot.content_hash,
            )

    def refresh_all_vaults(self, data_root: str | Path) -> dict[str, Any]:
        """Refresh all discovered vaults under a data root.

        Individual vault refresh failures are logged and summarized without
        aborting startup or manual workflow reload.
        """
        if not get_vault_state_enabled():
            return {
                "vault_state_enabled": False,
                "vault_state_refreshed": 0,
                "vault_state_failed": 0,
            }

        root = Path(data_root)
        refreshed = 0
        failed = 0
        files_created = 0
        files_changed = 0
        files_deleted = 0
        latest_sequence: int | None = None
        for vault_name in discover_vaults(str(root)):
            vault_path = root / vault_name
            try:
                result = self.refresh_vault(
                    vault_path,
                    vault_name=vault_name,
                    log_activity=False,
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.add_sink("validation").warning(
                    "vault_state_refresh_failed",
                    data={
                        "event": "vault_state_refresh_failed",
                        "vault_name": vault_name,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                continue
            refreshed += 1
            files_created += result.files_created
            files_changed += result.files_changed
            files_deleted += result.files_deleted
            if result.latest_sequence is not None:
                latest_sequence = result.latest_sequence

        changes_detected = files_created + files_changed + files_deleted

        should_log_activity = bool(failed or changes_detected)
        completion_logger = (
            logger.add_sink("validation")
            if should_log_activity
            else logger.set_sinks(["validation"])
        )
        completion_logger.info(
            "vault_state_refresh_all_completed",
            data={
                "event": "vault_state_refresh_all_completed",
                "data_root": str(root),
                "vaults_refreshed": refreshed,
                "vaults_failed": failed,
                "files_created": files_created,
                "files_changed": files_changed,
                "files_deleted": files_deleted,
                "changes_detected": changes_detected,
                "latest_sequence": latest_sequence,
            },
        )
        return {
            "vault_state_enabled": True,
            "vault_state_refreshed": refreshed,
            "vault_state_failed": failed,
            "vault_state_files_created": files_created,
            "vault_state_files_changed": files_changed,
            "vault_state_files_deleted": files_deleted,
            "vault_state_changes_detected": changes_detected,
            "vault_state_latest_sequence": latest_sequence,
        }

    @staticmethod
    def _relative_path(root: Path, path: Path) -> str:
        return str(path.relative_to(root)).replace("\\", "/")

    @staticmethod
    def _classify(relative_path: str) -> str:
        if relative_path.startswith(f"{ASSISTANTMD_ROOT_DIR}/{AUTHORING_DIR}/"):
            return "assistant_authoring"
        if relative_path.startswith(f"{ASSISTANTMD_ROOT_DIR}/"):
            return "assistant_generated"
        return "user_content"

    @staticmethod
    def _event_type(
        existing: VaultFile | None,
        content_hash: str,
        artifact_class: str,
    ) -> str:
        if existing is None:
            return "created"
        if existing.deleted_at is not None:
            return "created"
        if existing.artifact_class != artifact_class:
            return "classified"
        if existing.content_hash != content_hash:
            return "changed"
        return "observed"

    @staticmethod
    def _register_vault(
        session: Any, *, vault_id: str, vault_name: str, now: datetime
    ) -> None:
        # Register and acquire the write transaction before reading the manifest.
        # A scheduler refresh and the first mutation may discover a vault together.
        session.execute(
            insert(VaultRecord)
            .values(
                vault_id=vault_id,
                current_name=vault_name,
                first_seen_at=now,
                last_seen_at=now,
                missing_since=None,
            )
            .on_conflict_do_update(
                index_elements=[VaultRecord.vault_id],
                set_={
                    "current_name": vault_name,
                    "last_seen_at": now,
                    "missing_since": None,
                },
            )
        )

    @staticmethod
    def _append_event(
        session: Any,
        *,
        vault_id: str,
        vault_name: str,
        path: str,
        event_type: str,
        content_hash: str | None,
        artifact_class: str | None,
        now: datetime,
        metadata: dict[str, Any],
    ) -> VaultFileEvent:
        event = VaultFileEvent(
            vault_id=vault_id,
            vault_name=vault_name,
            path=path,
            event_type=event_type,
            content_hash=content_hash,
            artifact_class=artifact_class,
            observed_at=now,
            metadata_json=json.dumps(metadata, sort_keys=True) if metadata else None,
        )
        session.add(event)
        session.flush()
        return event

    @staticmethod
    def _log_file_event(
        *,
        event_type: str,
        vault_id: str,
        vault_name: str,
        path: str,
        content_hash: str | None,
        artifact_class: str | None,
        sequence: int | None,
    ) -> None:
        event_name = (
            "vault_state_file_deleted"
            if event_type == "deleted"
            else "vault_state_file_changed"
        )
        data = {
            "event": event_name,
            "vault_id": vault_id,
            "vault_name": vault_name,
            "path": path,
            "event_type": event_type,
            "content_hash": content_hash,
            "artifact_class": artifact_class,
            "sequence": sequence,
        }
        if get_debug_enabled():
            logger.add_sink("validation").info(event_name, data=data)
        else:
            logger.set_sinks(["validation"]).info(event_name, data=data)


def _snapshot_base_root() -> Path:
    return Path(get_system_database_path("vault_state")).parent / "vault_snapshots"


def _chat_session_id(kind: str, scope: str | None) -> str | None:
    prefix = "chat_session:"
    normalized_scope = scope or ""
    if kind == "chat" and normalized_scope.startswith(prefix):
        return normalized_scope[len(prefix) :]
    return None


def _mutation_item(activity: VaultActivity, row: VaultMutation) -> VaultMutationItem:
    metadata: dict[str, Any] = {}
    if row.metadata_json:
        try:
            parsed = json.loads(row.metadata_json)
            if isinstance(parsed, dict):
                metadata = parsed
        except (TypeError, ValueError):
            metadata = {}
    return VaultMutationItem(
        id=row.id,
        activity_id=activity.activity_id,
        operation_id=row.operation_id,
        task_id=activity.task_id,
        task_kind=activity.kind if activity.task_id else None,
        task_source=activity.source if activity.task_id else None,
        task_scope=activity.scope if activity.task_id else None,
        task_label=activity.label if activity.task_id else None,
        goal_id=activity.goal_id,
        step_id=activity.step_id,
        path=row.path,
        related_path=row.related_path,
        target_kind=row.target_kind,
        operation=row.operation,
        status=row.status,
        event_sequence=row.event_sequence,
        before_exists=bool(row.before_exists),
        before_hash=row.before_hash,
        before_snapshot_id=row.before_snapshot_id,
        after_exists=bool(row.after_exists),
        after_hash=row.after_hash,
        after_snapshot_id=row.after_snapshot_id,
        snapshot_ref=row.snapshot_ref,
        created_at=row.created_at,
        expires_at=row.expires_at,
        metadata=metadata,
    )


def _file_revision_item(
    mutation: VaultMutation,
    activity: VaultActivity,
    snapshot: FileSnapshot,
) -> VaultFileRevision:
    return VaultFileRevision(
        snapshot_id=snapshot.id,
        activity_id=activity.activity_id,
        activity_kind=activity.kind,
        activity_source=activity.source,
        activity_label=activity.label,
        task_id=activity.task_id,
        path=mutation.path,
        operation=mutation.operation,
        exists=bool(snapshot.exists),
        content_hash=snapshot.content_hash,
        snapshot_available=bool(snapshot.exists and snapshot.snapshot_ref),
        created_at=mutation.created_at,
        expires_at=snapshot.expires_at,
    )
