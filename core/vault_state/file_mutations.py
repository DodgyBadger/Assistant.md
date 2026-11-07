"""Recorded vault file mutation operations."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    get_current_execution_task,
    goal_context_from_metadata,
)
from core.utils.hash import hash_bytes, hash_file_bytes
from core.vault_state.activity import (
    VaultActivityContext,
    get_current_vault_activity,
    task_activity_id,
)
from core.vault_state.favorites import move_favorite_paths, remove_favorite_paths
from core.vault_state.identity import VaultIdentity, resolve_or_create_vault_identity
from core.vault_state.models import VaultActivity, VaultMutation
from core.vault_state.pathing import (
    normalize_vault_relative_path,
    resolve_vault_relative_path,
)
from core.vault_state.service import VaultStateService
from core.vault_state.snapshots import (
    compute_snapshot_expiration,
    compute_task_mutation_expiration,
    ensure_file_snapshot,
)

logger = UnifiedLogger(tag="vault-mutations")


class VaultMutationRejected(Exception):
    """Raised when a requested vault mutation is rejected or cannot be recorded safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


UNCERTAIN_MUTATION_STAGES = {"refresh", "persist"}
_MUTATION_LOCKS = tuple(threading.RLock() for _ in range(256))
_VAULT_MUTATION_LOCKS: tuple[_ReentrantReadWriteLock, ...]
_EXPECTED_HASH_UNSET = object()


class _ReentrantReadWriteLock:
    """Allow concurrent file mutations while excluding vault hierarchy changes."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._readers: dict[int, int] = {}
        self._writer: int | None = None
        self._writer_depth = 0
        self._waiting_writers = 0

    def acquire_read(self) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._writer == thread_id or thread_id in self._readers:
                self._readers[thread_id] = self._readers.get(thread_id, 0) + 1
                return
            while self._writer is not None or self._waiting_writers:
                self._condition.wait()
            self._readers[thread_id] = 1

    def release_read(self) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            depth = self._readers.get(thread_id, 0)
            if depth <= 1:
                self._readers.pop(thread_id, None)
                self._condition.notify_all()
            else:
                self._readers[thread_id] = depth - 1

    def acquire_write(self) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._writer == thread_id:
                self._writer_depth += 1
                return
            if thread_id in self._readers:
                raise RuntimeError("Cannot upgrade a vault mutation read lock")
            self._waiting_writers += 1
            try:
                while self._writer is not None or self._readers:
                    self._condition.wait()
                self._writer = thread_id
                self._writer_depth = 1
            finally:
                self._waiting_writers -= 1

    def release_write(self) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._writer != thread_id:
                raise RuntimeError(
                    "Current thread does not own the vault mutation lock"
                )
            self._writer_depth -= 1
            if self._writer_depth == 0:
                self._writer = None
                self._condition.notify_all()


_VAULT_MUTATION_LOCKS = tuple(_ReentrantReadWriteLock() for _ in range(64))


@contextmanager
def vault_file_mutation_lock(
    vault_path: str | Path,
    *paths: str | Path,
) -> Iterator[None]:
    """Share the vault hierarchy lock and serialize exact file targets."""
    vault_lock = _vault_mutation_lock(vault_path)
    vault_lock.acquire_read()
    try:
        with _exact_path_mutation_locks(*paths):
            yield
    finally:
        vault_lock.release_read()


@contextmanager
def vault_directory_mutation_lock(
    vault_path: str | Path,
    *paths: str | Path,
) -> Iterator[None]:
    """Exclude file mutations while changing the vault directory hierarchy."""
    vault_lock = _vault_mutation_lock(vault_path)
    vault_lock.acquire_write()
    try:
        with _exact_path_mutation_locks(*paths):
            yield
    finally:
        vault_lock.release_write()


@contextmanager
def _exact_path_mutation_locks(*paths: str | Path) -> Iterator[None]:
    """Acquire striped exact-path locks in deterministic order."""
    lock_indexes = sorted(
        {hash(str(Path(path).resolve())) % len(_MUTATION_LOCKS) for path in paths}
    )
    locks = [_MUTATION_LOCKS[index] for index in lock_indexes]
    for lock in locks:
        lock.acquire()
    try:
        yield
    finally:
        for lock in reversed(locks):
            lock.release()


def _vault_mutation_lock(vault_path: str | Path) -> _ReentrantReadWriteLock:
    resolved = str(Path(vault_path).resolve())
    return _VAULT_MUTATION_LOCKS[hash(resolved) % len(_VAULT_MUTATION_LOCKS)]


@dataclass(frozen=True)
class RecordedMutationResult:
    """Result of a recorded vault mutation."""

    vault_id: str
    vault_name: str
    path: str
    related_path: str | None
    operation: str
    before_exists: bool
    before_hash: str | None
    after_exists: bool
    after_hash: str | None
    task_id: str | None
    event_sequence: int | None
    before_snapshot_id: int | None
    snapshot_ref: str | None


@dataclass(frozen=True)
class FileStateRestore:
    """One exact desired file state guarded by its expected current state."""

    path: str
    expected_exists: bool
    expected_sha256: str | None
    content: bytes | None = None
    content_path: Path | None = None
    content_sha256: str | None = None

    @property
    def restore_exists(self) -> bool:
        return self.content is not None or self.content_path is not None


@dataclass(frozen=True)
class ResolvedFileStateRestore:
    """A restore request resolved to one vault-owned filesystem path."""

    request: FileStateRestore
    relative_path: str
    full_path: Path


@dataclass(frozen=True)
class CapturedFileState:
    """The displaced state and durable revision attribution for compensation."""

    exists: bool
    sha256: str | None
    compensation_path: Path | None
    snapshot_id: int | None = None
    snapshot_ref: str | None = None


@dataclass(frozen=True)
class FileStateTransition:
    """One verified before-to-after transition from an atomic state restore."""

    path: str
    before_exists: bool
    before_sha256: str | None
    after_exists: bool
    after_sha256: str | None
    event_sequence: int | None


@dataclass(frozen=True)
class SnapshotAttribution:
    """Ownership and policy for one pre-mutation snapshot capture."""

    activity_id: str | None
    task_id: str | None
    kind: str | None
    source: str | None
    scope: str | None
    label: str | None
    purpose: str
    snapshot_source: str
    scope_kind: str
    scope_id: str


@dataclass(frozen=True)
class DirectoryMoveResult:
    """Result of one observed directory move inside a vault."""

    vault_id: str
    vault_name: str
    source_path: str
    destination_path: str
    descendant_file_count: int
    descendant_directory_count: int
    event_sequence: int | None


def write_vault_file(
    *,
    vault_path: str | Path,
    path: str,
    content: str,
    fail_if_exists: bool = True,
    markdown_only: bool = False,
    warn_without_task: bool = True,
) -> RecordedMutationResult:
    """Create or overwrite a vault file while recording attributed mutation metadata."""
    return mutate_vault_file(
        vault_path=vault_path,
        path=path,
        operation="write",
        mutator=lambda full_path: full_path.write_text(content, encoding="utf-8"),
        fail_if_exists=fail_if_exists,
        markdown_only=markdown_only,
        create_parent=True,
        warn_without_task=warn_without_task,
    )


def write_vault_file_bytes(
    *,
    vault_path: str | Path,
    path: str,
    content: bytes,
    fail_if_exists: bool = True,
    warn_without_task: bool = True,
) -> RecordedMutationResult:
    """Create or overwrite a binary vault file while recording mutation metadata."""
    return mutate_vault_file(
        vault_path=vault_path,
        path=path,
        operation="write",
        mutator=lambda full_path: _write_vault_file_bytes(
            full_path,
            content,
            fail_if_exists=fail_if_exists,
        ),
        fail_if_exists=fail_if_exists,
        create_parent=True,
        warn_without_task=warn_without_task,
    )


def _write_vault_file_bytes(
    full_path: Path,
    content: bytes,
    *,
    fail_if_exists: bool,
) -> None:
    """Write bytes without leaving a partial create-only destination."""
    if not fail_if_exists:
        full_path.write_bytes(content)
        return

    try:
        destination = full_path.open("xb")
    except FileExistsError as exc:
        raise VaultMutationRejected(
            "file_exists",
            f"Cannot mutate '{full_path.name}' - file already exists.",
        ) from exc

    try:
        with destination:
            destination.write(content)
    except Exception:
        try:
            full_path.unlink(missing_ok=True)
        except OSError as cleanup_error:
            raise VaultMutationRejected(
                "mutation_state_uncertain",
                (
                    f"Binary write for '{full_path.name}' failed and its partial "
                    f"destination could not be removed: {cleanup_error}"
                ),
            ) from cleanup_error
        raise


def append_vault_file(
    *,
    vault_path: str | Path,
    path: str,
    content: str,
    markdown_only: bool = False,
) -> RecordedMutationResult:
    """Append text to an existing vault file while recording mutation metadata."""

    def append_content(full_path: Path) -> None:
        with full_path.open("a", encoding="utf-8") as file:
            file.write(content)

    return mutate_vault_file(
        vault_path=vault_path,
        path=path,
        operation="append",
        mutator=append_content,
        require_exists=True,
        markdown_only=markdown_only,
    )


def replace_vault_file_content(
    *,
    vault_path: str | Path,
    path: str,
    content: str,
    operation: str,
    markdown_only: bool = False,
) -> RecordedMutationResult:
    """Replace the full contents of an existing vault file and record the mutation."""

    def write_content(full_path: Path) -> None:
        full_path.write_text(content, encoding="utf-8")

    return mutate_vault_file(
        vault_path=vault_path,
        path=path,
        operation=operation,
        mutator=write_content,
        require_exists=True,
        markdown_only=markdown_only,
    )


def resolve_vault_deletion_target(*, vault_path: str | Path, path: str) -> Path:
    """Resolve a deletion target inside the vault while protecting the root."""
    vault_root = Path(vault_path).resolve()
    target = resolve_vault_relative_path(vault_path=vault_root, path=path)
    if target == vault_root:
        raise VaultMutationRejected("invalid_target", "Cannot delete the vault root.")
    return target


def delete_vault_file(
    *,
    vault_path: str | Path,
    path: str,
    markdown_only: bool = False,
    warn_without_task: bool = True,
) -> RecordedMutationResult:
    """Delete an existing vault file while recording mutation metadata."""
    return mutate_vault_file(
        vault_path=vault_path,
        path=path,
        operation="delete",
        mutator=lambda full_path: os.remove(full_path),
        require_exists=True,
        markdown_only=markdown_only,
        warn_without_task=warn_without_task,
    )


def restore_vault_file(
    *,
    vault_path: str | Path,
    path: str,
    content: bytes | None,
    expected_sha256: str | None,
) -> RecordedMutationResult:
    """Restore one exact file state while retaining the displaced state."""
    return restore_vault_file_states(
        vault_path=vault_path,
        states=(
            FileStateRestore(
                path=path,
                expected_exists=expected_sha256 is not None,
                expected_sha256=expected_sha256,
                content=content,
            ),
        ),
        operation="restore_revision",
    )[0]


def restore_vault_file_states(
    *,
    vault_path: str | Path,
    states: tuple[FileStateRestore, ...],
    operation: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[RecordedMutationResult, ...]:
    """Atomically restore exact file states under one explicit activity."""
    if not states:
        raise VaultMutationRejected("empty_restore", "No file states were provided.")
    context = get_current_vault_activity()
    if context is None:
        raise VaultMutationRejected(
            "missing_activity_context",
            "Exact file-state restoration requires an explicit vault activity.",
        )

    vault_root = Path(vault_path).resolve()
    normalized = _resolve_file_state_restores(vault_root=vault_root, states=states)

    with vault_file_mutation_lock(
        vault_root,
        *(item.full_path for item in normalized),
    ):
        return _restore_vault_file_states_locked(
            vault_root=vault_root,
            states=tuple(normalized),
            operation=operation,
            metadata=metadata,
            context=context,
        )


def restore_file_states_atomically(
    *,
    vault_path: str | Path,
    states: tuple[FileStateRestore, ...],
) -> tuple[FileStateTransition, ...]:
    """Restore exact file states with shared locking, verification, and compensation."""
    if not states:
        raise VaultMutationRejected("empty_restore", "No file states were provided.")
    vault_root = Path(vault_path).resolve()
    resolved = _resolve_file_state_restores(vault_root=vault_root, states=states)
    with vault_file_mutation_lock(
        vault_root,
        *(item.full_path for item in resolved),
    ):
        with tempfile.TemporaryDirectory(prefix="assistantmd-restore-") as temp_dir:
            before_states = _capture_restore_before_states(
                states=resolved,
                temp_root=Path(temp_dir),
            )
            return cast(
                tuple[FileStateTransition, ...],
                _run_file_state_restore_transaction(
                    service=VaultStateService(),
                    vault_root=vault_root,
                    vault_name=vault_root.name,
                    states=resolved,
                    before_states=before_states,
                ),
            )


def _resolve_file_state_restores(
    *,
    vault_root: Path,
    states: tuple[FileStateRestore, ...],
) -> tuple[ResolvedFileStateRestore, ...]:
    resolved: list[ResolvedFileStateRestore] = []
    seen_paths: set[str] = set()
    for state in states:
        if state.content is not None and state.content_path is not None:
            raise VaultMutationRejected(
                "invalid_restore_state",
                f"File state restoration has multiple content sources: {state.path}",
            )
        relative_path = normalize_vault_relative_path(state.path)
        if relative_path in seen_paths:
            raise VaultMutationRejected(
                "duplicate_restore_path",
                f"File state restoration contains a duplicate path: {relative_path}",
            )
        seen_paths.add(relative_path)
        resolved.append(
            ResolvedFileStateRestore(
                request=state,
                relative_path=relative_path,
                full_path=resolve_vault_relative_path(
                    vault_path=vault_root,
                    path=relative_path,
                    markdown_only=False,
                ),
            )
        )
    return tuple(resolved)


def _restore_vault_file_states_locked(
    *,
    vault_root: Path,
    states: tuple[ResolvedFileStateRestore, ...],
    operation: str,
    metadata: dict[str, Any] | None,
    context: VaultActivityContext,
) -> tuple[RecordedMutationResult, ...]:
    """Restore exact states while holding every affected path lock."""
    identity = resolve_or_create_vault_identity(vault_root)
    vault_name = vault_root.name
    service = VaultStateService()
    created_at = datetime.now(UTC)
    snapshot_expires_at = compute_snapshot_expiration(created_at)
    mutation_expires_at = compute_task_mutation_expiration(created_at)
    operation_id = f"operation_{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="assistantmd-restore-") as temp_dir:
        before_states = _capture_restore_before_states(
            states=states,
            temp_root=Path(temp_dir),
        )
        service.ensure_activity(
            context=context,
            vault_path=vault_root,
            vault_name=vault_name,
            created_at=created_at,
        )
        before_states = _record_restore_snapshots(
            service=service,
            states=states,
            before_states=before_states,
            context=context,
            identity=identity,
            vault_root=vault_root,
            vault_name=vault_name,
            created_at=created_at,
            expires_at=snapshot_expires_at,
        )

        def persist_restore(
            transitions: tuple[FileStateTransition, ...],
        ) -> tuple[RecordedMutationResult, ...]:
            results = _build_recorded_restore_results(
                transitions=transitions,
                before_states=before_states,
                identity=identity,
                vault_name=vault_name,
                operation=operation,
                task_id=context.task_id,
            )
            _persist_mutation_batch(
                service=service,
                context=context,
                results=results,
                vault_root=vault_root,
                operation_id=operation_id,
                created_at=created_at,
                expires_at=mutation_expires_at,
                metadata=metadata,
            )
            return results

        results = cast(
            tuple[RecordedMutationResult, ...],
            _run_file_state_restore_transaction(
                service=service,
                vault_root=vault_root,
                vault_name=vault_name,
                states=states,
                before_states=before_states,
                finalize=persist_restore,
            ),
        )
        return results


def _capture_restore_before_states(
    *,
    states: tuple[ResolvedFileStateRestore, ...],
    temp_root: Path,
) -> dict[str, CapturedFileState]:
    captured: dict[str, CapturedFileState] = {}
    for index, item in enumerate(states):
        current_exists = item.full_path.exists()
        if current_exists and not item.full_path.is_file():
            raise VaultMutationRejected(
                "file_conflict",
                f"Cannot restore '{item.relative_path}' because it is no longer a file.",
            )
        current_hash = (
            hash_file_bytes(item.full_path, length=None) if current_exists else None
        )
        if (
            current_exists != item.request.expected_exists
            or current_hash != item.request.expected_sha256
        ):
            raise VaultMutationRejected(
                "file_conflict",
                f"Cannot restore '{item.relative_path}' because the current file state changed.",
            )
        compensation_path = temp_root / f"{index}.bin" if current_exists else None
        if compensation_path is not None:
            shutil.copy2(item.full_path, compensation_path)
            if hash_file_bytes(compensation_path, length=None) != current_hash:
                raise VaultMutationRejected(
                    "file_conflict",
                    f"Cannot restore '{item.relative_path}' because it changed during preflight.",
                )
        captured[item.relative_path] = CapturedFileState(
            exists=current_exists,
            sha256=current_hash,
            compensation_path=compensation_path,
        )
    return captured


def _record_restore_snapshots(
    *,
    service: VaultStateService,
    states: tuple[ResolvedFileStateRestore, ...],
    before_states: dict[str, CapturedFileState],
    context: VaultActivityContext,
    identity: VaultIdentity,
    vault_root: Path,
    vault_name: str,
    created_at: datetime,
    expires_at: datetime | None,
) -> dict[str, CapturedFileState]:
    recorded = dict(before_states)
    with service.SessionFactory() as session:
        for item in states:
            before = before_states[item.relative_path]
            snapshot = ensure_file_snapshot(
                session=session,
                activity_id=context.activity_id,
                task_id=context.task_id,
                task_kind=context.kind,
                task_source=context.source,
                task_scope=context.scope,
                task_label=context.label,
                vault_id=identity.vault_id,
                vault_name=vault_name,
                vault_root=vault_root,
                relative_path=item.relative_path,
                before_exists=before.exists,
                source_path=item.full_path,
                purpose="revision",
                source="activity_mutation_before",
                scope_kind="activity",
                scope_id=context.activity_id,
                created_at=created_at,
                expires_at=expires_at,
            )
            recorded[item.relative_path] = CapturedFileState(
                exists=before.exists,
                sha256=before.sha256,
                compensation_path=before.compensation_path,
                snapshot_id=snapshot.file_snapshot_id,
                snapshot_ref=snapshot.snapshot_ref,
            )
        session.commit()
    return recorded


def _apply_file_state_restores(states: tuple[ResolvedFileStateRestore, ...]) -> None:
    for item in states:
        _restore_content_hash(item.request)
    for item in states:
        if not item.request.restore_exists and item.full_path.exists():
            item.full_path.unlink()
    for item in states:
        if not item.request.restore_exists:
            continue
        item.full_path.parent.mkdir(parents=True, exist_ok=True)
        if item.request.content_path is not None:
            shutil.copy2(item.request.content_path, item.full_path)
        else:
            item.full_path.write_bytes(item.request.content or b"")


def _run_file_state_restore_transaction(
    *,
    service: VaultStateService,
    vault_root: Path,
    vault_name: str,
    states: tuple[ResolvedFileStateRestore, ...],
    before_states: dict[str, CapturedFileState],
    finalize: Callable[[tuple[FileStateTransition, ...]], Any] | None = None,
) -> Any:
    try:
        _apply_file_state_restores(states)
        refresh = service.refresh_vault(vault_root, vault_name=vault_name)
        transitions = _build_file_state_transitions(
            states=states,
            before_states=before_states,
            event_sequence=refresh.latest_sequence,
        )
        return finalize(transitions) if finalize is not None else transitions
    except Exception as exc:
        _compensate_file_state_restores(
            service=service,
            vault_root=vault_root,
            vault_name=vault_name,
            states=states,
            before_states=before_states,
            cause=exc,
        )
        if isinstance(exc, VaultMutationRejected):
            raise
        raise VaultMutationRejected(
            "restore_failed",
            f"File-state restoration failed and was compensated: {exc}",
        ) from exc


def _build_file_state_transitions(
    *,
    states: tuple[ResolvedFileStateRestore, ...],
    before_states: dict[str, CapturedFileState],
    event_sequence: int | None,
) -> tuple[FileStateTransition, ...]:
    transitions: list[FileStateTransition] = []
    for item in states:
        after_exists = item.full_path.is_file()
        after_hash = (
            hash_file_bytes(item.full_path, length=None) if after_exists else None
        )
        expected_after_hash = _restore_content_hash(item.request)
        if (
            after_exists != item.request.restore_exists
            or after_hash != expected_after_hash
        ):
            raise VaultMutationRejected(
                "file_conflict",
                f"Cannot complete restoration because '{item.relative_path}' changed during the operation.",
            )
        before = before_states[item.relative_path]
        transitions.append(
            FileStateTransition(
                path=item.relative_path,
                before_exists=before.exists,
                before_sha256=before.sha256,
                after_exists=after_exists,
                after_sha256=after_hash,
                event_sequence=event_sequence,
            )
        )
    return tuple(transitions)


def _build_recorded_restore_results(
    *,
    transitions: tuple[FileStateTransition, ...],
    before_states: dict[str, CapturedFileState],
    identity: VaultIdentity,
    vault_name: str,
    operation: str,
    task_id: str | None,
) -> tuple[RecordedMutationResult, ...]:
    return tuple(
        RecordedMutationResult(
            vault_id=identity.vault_id,
            vault_name=vault_name,
            path=transition.path,
            related_path=None,
            operation=operation,
            before_exists=transition.before_exists,
            before_hash=transition.before_sha256,
            after_exists=transition.after_exists,
            after_hash=transition.after_sha256,
            task_id=task_id,
            event_sequence=transition.event_sequence,
            before_snapshot_id=before_states[transition.path].snapshot_id,
            snapshot_ref=before_states[transition.path].snapshot_ref,
        )
        for transition in transitions
    )


def _restore_content_hash(state: FileStateRestore) -> str | None:
    content_hash: str | None
    if state.content_path is not None:
        content_hash = hash_file_bytes(state.content_path, length=None)
    elif state.content is not None:
        content_hash = hash_bytes(state.content, length=None)
    else:
        content_hash = None
    if state.content_sha256 is not None and content_hash != state.content_sha256:
        raise VaultMutationRejected(
            "snapshot_invalid",
            f"Restore content failed integrity checking: {state.path}",
        )
    return content_hash


def _compensate_file_state_restores(
    *,
    service: VaultStateService,
    vault_root: Path,
    vault_name: str,
    states: tuple[ResolvedFileStateRestore, ...],
    before_states: dict[str, CapturedFileState],
    cause: Exception,
) -> None:
    try:
        for item in states:
            before = before_states[item.relative_path]
            if before.exists:
                if before.compensation_path is None:
                    raise RuntimeError(
                        f"Missing compensation content for '{item.relative_path}'"
                    )
                item.full_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(before.compensation_path, item.full_path)
            elif item.full_path.exists():
                item.full_path.unlink()
        service.refresh_vault(vault_root, vault_name=vault_name)
    except Exception as rollback_exc:  # pragma: no cover - requires filesystem failure
        raise VaultMutationRejected(
            "mutation_state_uncertain",
            f"File-state restoration failed and compensation was incomplete: {rollback_exc}",
        ) from cause


def move_vault_directory(
    *,
    vault_path: str | Path,
    path: str,
    destination: str,
) -> DirectoryMoveResult:
    """Move one directory tree and record the user-visible intent as one event."""
    vault_root = Path(vault_path).resolve()
    source_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=path,
        markdown_only=False,
    )
    destination_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=destination,
        markdown_only=False,
    )
    with vault_directory_mutation_lock(vault_root, source_path, destination_path):
        return _move_vault_directory_locked(
            vault_path=vault_root,
            path=path,
            destination=destination,
        )


def _move_vault_directory_locked(
    *,
    vault_path: str | Path,
    path: str,
    destination: str,
) -> DirectoryMoveResult:
    """Move and record a directory while holding the vault hierarchy lock."""
    vault_root = Path(vault_path).resolve()
    source_relative = normalize_vault_relative_path(path)
    destination_relative = normalize_vault_relative_path(destination)
    if not source_relative or not destination_relative:
        raise VaultMutationRejected(
            "invalid_target",
            "Cannot move the vault root directory.",
        )
    source_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=source_relative,
        markdown_only=False,
    )
    destination_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=destination_relative,
        markdown_only=False,
    )

    with vault_directory_mutation_lock(vault_root, source_path, destination_path):
        if not source_path.exists():
            raise VaultMutationRejected(
                "source_not_found",
                f"Cannot move '{source_relative}' - source directory does not exist.",
            )
        if not source_path.is_dir():
            raise VaultMutationRejected(
                "source_not_directory",
                f"Cannot move '{source_relative}' as a directory - source is not a directory.",
            )
        if destination_path == source_path:
            raise VaultMutationRejected(
                "source_equals_destination",
                "Directory source and destination must be different.",
            )
        if source_path in destination_path.parents:
            raise VaultMutationRejected(
                "destination_inside_source",
                f"Cannot move '{source_relative}' inside itself.",
            )
        if destination_path.exists():
            raise VaultMutationRejected(
                "destination_exists",
                f"Cannot move '{source_relative}' - destination already exists.",
            )

        descendant_file_count, descendant_directory_count = (
            _directory_descendant_counts(source_path)
        )
        identity = resolve_or_create_vault_identity(vault_root)
        vault_name = vault_root.name
        service = VaultStateService()
        stage = "mutate"
        try:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source_path, destination_path)
            stage = "refresh"
            move_favorite_paths(vault_root, source_relative, destination_relative)
            refresh = service.refresh_vault(vault_root, vault_name=vault_name)
        except Exception as exc:
            logger.add_sink("validation").warning(
                "vault_directory_move_failed",
                data={
                    "event": "vault_directory_move_failed",
                    "vault_id": identity.vault_id,
                    "vault_name": vault_name,
                    "source_path": source_relative,
                    "destination_path": destination_relative,
                    "stage": stage,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            if stage == "refresh":
                raise VaultMutationRejected(
                    "mutation_state_uncertain",
                    (
                        f"Directory move from '{source_relative}' to '{destination_relative}' "
                        f"was applied, but vault-state refresh failed: {exc}"
                    ),
                ) from exc
            raise

    result = DirectoryMoveResult(
        vault_id=identity.vault_id,
        vault_name=vault_name,
        source_path=source_relative,
        destination_path=destination_relative,
        descendant_file_count=descendant_file_count,
        descendant_directory_count=descendant_directory_count,
        event_sequence=refresh.latest_sequence,
    )
    logger.add_sink("validation").info(
        "vault_directory_move_completed",
        data={
            "event": "vault_directory_move_completed",
            "vault_id": result.vault_id,
            "vault_name": result.vault_name,
            "source_path": result.source_path,
            "destination_path": result.destination_path,
            "descendant_file_count": result.descendant_file_count,
            "descendant_directory_count": result.descendant_directory_count,
            "event_sequence": result.event_sequence,
        },
    )
    record_vault_directory_mutation(
        vault_path=vault_root,
        path=source_relative,
        related_path=destination_relative,
        operation="move",
        before_exists=True,
        after_exists=False,
        event_sequence=result.event_sequence,
        metadata={
            "descendant_file_count": result.descendant_file_count,
            "descendant_directory_count": result.descendant_directory_count,
        },
    )
    return result


def record_vault_directory_mutation(
    *,
    vault_path: str | Path,
    path: str,
    operation: str,
    before_exists: bool,
    after_exists: bool,
    related_path: str | None = None,
    event_sequence: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record one logical directory operation under current attribution."""
    vault_root = Path(vault_path).resolve()
    identity = resolve_or_create_vault_identity(vault_root)
    task = get_current_execution_task()
    created_at = datetime.now(UTC)
    result = RecordedMutationResult(
        vault_id=identity.vault_id,
        vault_name=vault_root.name,
        path=normalize_vault_relative_path(path),
        related_path=(
            normalize_vault_relative_path(related_path) if related_path else None
        ),
        operation=operation,
        before_exists=before_exists,
        before_hash=None,
        after_exists=after_exists,
        after_hash=None,
        task_id=task.task_id if task is not None else None,
        event_sequence=event_sequence,
        before_snapshot_id=None,
        snapshot_ref=None,
    )
    _persist_or_log_mutation(
        service=VaultStateService(),
        task=task,
        result=result,
        vault_root=vault_root,
        operation_id=f"operation_{uuid.uuid4().hex}",
        created_at=created_at,
        expires_at=compute_task_mutation_expiration(created_at),
        target_kind="directory",
        metadata=metadata,
    )


def move_vault_file(
    *,
    vault_path: str | Path,
    path: str,
    destination: str,
    overwrite: bool = False,
    markdown_only: bool = False,
) -> tuple[RecordedMutationResult, RecordedMutationResult]:
    """Move a vault file while recording source and destination file mutations."""
    vault_root = Path(vault_path).resolve()
    source_relative = normalize_vault_relative_path(path)
    destination_relative = normalize_vault_relative_path(destination)
    source_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=source_relative,
        markdown_only=markdown_only,
    )
    destination_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=destination_relative,
        markdown_only=markdown_only,
    )
    with vault_file_mutation_lock(vault_root, source_path, destination_path):
        return _move_vault_file_locked(
            vault_root=vault_root,
            source_relative=source_relative,
            destination_relative=destination_relative,
            source_path=source_path,
            destination_path=destination_path,
            overwrite=overwrite,
        )


def _move_vault_file_locked(
    *,
    vault_root: Path,
    source_relative: str,
    destination_relative: str,
    source_path: Path,
    destination_path: Path,
    overwrite: bool,
) -> tuple[RecordedMutationResult, RecordedMutationResult]:
    """Move a vault file while holding both path mutation locks."""
    if not source_path.exists():
        raise VaultMutationRejected(
            "source_not_found",
            f"Cannot move '{source_relative}' - source file does not exist.",
        )
    if destination_path.exists() and not overwrite:
        raise VaultMutationRejected(
            "destination_exists",
            f"Cannot move '{source_relative}' - destination file already exists.",
        )

    source_before_hash = hash_file_bytes(source_path, length=None)
    destination_before_exists = destination_path.exists()
    destination_before_hash = (
        hash_file_bytes(destination_path, length=None)
        if destination_before_exists
        else None
    )
    identity = resolve_or_create_vault_identity(vault_root)
    vault_name = vault_root.name
    task = get_current_execution_task()
    service = VaultStateService()
    created_at = datetime.now(UTC)
    snapshot_expires_at = compute_snapshot_expiration(created_at)
    mutation_expires_at = compute_task_mutation_expiration(created_at)
    operation_id = f"operation_{uuid.uuid4().hex}"
    source_snapshot_ref = None
    source_snapshot_id = None
    destination_snapshot_ref = None
    destination_snapshot_id = None

    stage = "snapshot"
    try:
        snapshot_attribution = _snapshot_attribution(task)
        if snapshot_attribution is not None:
            _ensure_explicit_activity(
                service=service,
                attribution=snapshot_attribution,
                vault_root=vault_root,
                vault_name=vault_name,
                created_at=created_at,
            )
            with service.SessionFactory() as session:
                source_snapshot = ensure_file_snapshot(
                    session=session,
                    activity_id=snapshot_attribution.activity_id,
                    task_id=snapshot_attribution.task_id,
                    task_kind=snapshot_attribution.kind,
                    task_source=snapshot_attribution.source,
                    task_scope=snapshot_attribution.scope,
                    task_label=snapshot_attribution.label,
                    vault_id=identity.vault_id,
                    vault_name=vault_name,
                    vault_root=vault_root,
                    relative_path=source_relative,
                    before_exists=True,
                    source_path=source_path,
                    purpose=snapshot_attribution.purpose,
                    source=snapshot_attribution.snapshot_source,
                    scope_kind=snapshot_attribution.scope_kind,
                    scope_id=snapshot_attribution.scope_id,
                    created_at=created_at,
                    expires_at=snapshot_expires_at,
                )
                source_snapshot_ref = source_snapshot.snapshot_ref
                source_snapshot_id = source_snapshot.file_snapshot_id
                destination_snapshot = ensure_file_snapshot(
                    session=session,
                    activity_id=snapshot_attribution.activity_id,
                    task_id=snapshot_attribution.task_id,
                    task_kind=snapshot_attribution.kind,
                    task_source=snapshot_attribution.source,
                    task_scope=snapshot_attribution.scope,
                    task_label=snapshot_attribution.label,
                    vault_id=identity.vault_id,
                    vault_name=vault_name,
                    vault_root=vault_root,
                    relative_path=destination_relative,
                    before_exists=destination_before_exists,
                    source_path=destination_path,
                    purpose=snapshot_attribution.purpose,
                    source=snapshot_attribution.snapshot_source,
                    scope_kind=snapshot_attribution.scope_kind,
                    scope_id=snapshot_attribution.scope_id,
                    created_at=created_at,
                    expires_at=snapshot_expires_at,
                )
                destination_snapshot_ref = destination_snapshot.snapshot_ref
                destination_snapshot_id = destination_snapshot.file_snapshot_id
                session.commit()

        stage = "mutate"
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source_path, destination_path)
        stage = "refresh"
        move_favorite_paths(vault_root, source_relative, destination_relative)

        destination_after_hash = hash_file_bytes(destination_path, length=None)
        stage = "refresh"
        refresh = service.refresh_vault(vault_root, vault_name=vault_name)
        event_sequence = refresh.latest_sequence

        source_result = RecordedMutationResult(
            vault_id=identity.vault_id,
            vault_name=vault_name,
            path=source_relative,
            related_path=destination_relative,
            operation="move",
            before_exists=True,
            before_hash=source_before_hash,
            after_exists=source_path.exists(),
            after_hash=(
                hash_file_bytes(source_path, length=None)
                if source_path.exists()
                else None
            ),
            task_id=task.task_id if task is not None else None,
            event_sequence=event_sequence,
            before_snapshot_id=source_snapshot_id,
            snapshot_ref=source_snapshot_ref,
        )
        destination_result = RecordedMutationResult(
            vault_id=identity.vault_id,
            vault_name=vault_name,
            path=destination_relative,
            related_path=source_relative,
            operation="move",
            before_exists=destination_before_exists,
            before_hash=destination_before_hash,
            after_exists=True,
            after_hash=destination_after_hash,
            task_id=task.task_id if task is not None else None,
            event_sequence=event_sequence,
            before_snapshot_id=destination_snapshot_id,
            snapshot_ref=destination_snapshot_ref,
        )
        stage = "persist"
        _persist_or_log_mutation(
            service=service,
            task=task,
            result=source_result,
            vault_root=vault_root,
            operation_id=operation_id,
            created_at=created_at,
            expires_at=mutation_expires_at,
        )
        _persist_or_log_mutation(
            service=service,
            task=task,
            result=destination_result,
            vault_root=vault_root,
            operation_id=operation_id,
            created_at=created_at,
            expires_at=mutation_expires_at,
        )
    except Exception as exc:
        _log_mutation_failed(
            task=task,
            vault_id=identity.vault_id,
            vault_name=vault_name,
            path=source_relative,
            related_path=destination_relative,
            operation="move",
            stage=stage,
            before_exists=True,
            before_hash=source_before_hash,
            before_snapshot_id=source_snapshot_id,
            error=exc,
        )
        if stage in UNCERTAIN_MUTATION_STAGES:
            raise VaultMutationRejected(
                "mutation_state_uncertain",
                (
                    f"Move from '{source_relative}' to '{destination_relative}' may have been applied, "
                    f"but vault-state recording failed during {stage}: {exc}"
                ),
            ) from exc
        raise
    return source_result, destination_result


def mutate_vault_file(
    *,
    vault_path: str | Path,
    path: str,
    operation: str,
    mutator: Callable[[Path], object],
    require_exists: bool = False,
    fail_if_exists: bool = False,
    markdown_only: bool = False,
    create_parent: bool = False,
    warn_without_task: bool = True,
    expected_before_hash: str | None | object = _EXPECTED_HASH_UNSET,
) -> RecordedMutationResult:
    """Mutate one vault file while recording task-scoped mutation metadata."""
    vault_root = Path(vault_path).resolve()
    relative_path = normalize_vault_relative_path(path)
    full_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=relative_path,
        markdown_only=markdown_only,
    )
    with vault_file_mutation_lock(vault_root, full_path):
        return _mutate_vault_file_locked(
            vault_root=vault_root,
            relative_path=relative_path,
            full_path=full_path,
            operation=operation,
            mutator=mutator,
            require_exists=require_exists,
            fail_if_exists=fail_if_exists,
            create_parent=create_parent,
            warn_without_task=warn_without_task,
            expected_before_hash=expected_before_hash,
        )


def _mutate_vault_file_locked(
    *,
    vault_root: Path,
    relative_path: str,
    full_path: Path,
    operation: str,
    mutator: Callable[[Path], object],
    require_exists: bool,
    fail_if_exists: bool,
    create_parent: bool,
    warn_without_task: bool,
    expected_before_hash: str | None | object,
) -> RecordedMutationResult:
    """Mutate one vault file while holding its path mutation lock."""
    before_exists = full_path.exists()
    if require_exists and not before_exists:
        raise VaultMutationRejected(
            "file_not_found",
            f"Cannot mutate '{relative_path}' - file does not exist.",
        )
    if before_exists and fail_if_exists:
        raise VaultMutationRejected(
            "file_exists",
            f"Cannot mutate '{relative_path}' - file already exists.",
        )
    before_hash = hash_file_bytes(full_path, length=None) if before_exists else None
    if (
        expected_before_hash is not _EXPECTED_HASH_UNSET
        and expected_before_hash != before_hash
    ):
        raise VaultMutationRejected(
            "file_conflict",
            f"Cannot restore '{relative_path}' because the current file state changed.",
        )
    identity = resolve_or_create_vault_identity(vault_root)
    vault_name = vault_root.name
    task = get_current_execution_task()
    service = VaultStateService()
    snapshot_ref = None
    before_snapshot_id = None
    created_at = datetime.now(UTC)
    snapshot_expires_at = compute_snapshot_expiration(created_at)
    mutation_expires_at = compute_task_mutation_expiration(created_at)
    operation_id = f"operation_{uuid.uuid4().hex}"

    stage = "snapshot"
    try:
        snapshot_attribution = _snapshot_attribution(task)
        if snapshot_attribution is not None:
            _ensure_explicit_activity(
                service=service,
                attribution=snapshot_attribution,
                vault_root=vault_root,
                vault_name=vault_name,
                created_at=created_at,
            )
            with service.SessionFactory() as session:
                snapshot = ensure_file_snapshot(
                    session=session,
                    activity_id=snapshot_attribution.activity_id,
                    task_id=snapshot_attribution.task_id,
                    task_kind=snapshot_attribution.kind,
                    task_source=snapshot_attribution.source,
                    task_scope=snapshot_attribution.scope,
                    task_label=snapshot_attribution.label,
                    vault_id=identity.vault_id,
                    vault_name=vault_name,
                    vault_root=vault_root,
                    relative_path=relative_path,
                    before_exists=before_exists,
                    source_path=full_path,
                    purpose=snapshot_attribution.purpose,
                    source=snapshot_attribution.snapshot_source,
                    scope_kind=snapshot_attribution.scope_kind,
                    scope_id=snapshot_attribution.scope_id,
                    created_at=created_at,
                    expires_at=snapshot_expires_at,
                )
                snapshot_ref = snapshot.snapshot_ref
                before_snapshot_id = snapshot.file_snapshot_id
                session.commit()

        stage = "mutate"
        if create_parent:
            full_path.parent.mkdir(parents=True, exist_ok=True)
        mutator(full_path)

        after_exists = full_path.exists()
        after_hash = hash_file_bytes(full_path, length=None) if after_exists else None

        stage = "refresh"
        if not after_exists:
            remove_favorite_paths(vault_root, [relative_path])
        refresh = service.refresh_vault(vault_root, vault_name=vault_name)
        event_sequence = refresh.latest_sequence
        result = RecordedMutationResult(
            vault_id=identity.vault_id,
            vault_name=vault_name,
            path=relative_path,
            related_path=None,
            operation=operation,
            before_exists=before_exists,
            before_hash=before_hash,
            after_exists=after_exists,
            after_hash=after_hash,
            task_id=task.task_id if task is not None else None,
            event_sequence=event_sequence,
            before_snapshot_id=before_snapshot_id,
            snapshot_ref=snapshot_ref,
        )

        stage = "persist"
        _persist_or_log_mutation(
            service=service,
            task=task,
            result=result,
            vault_root=vault_root,
            operation_id=operation_id,
            created_at=created_at,
            expires_at=mutation_expires_at,
            warn_without_task=warn_without_task,
        )
    except Exception as exc:
        _log_mutation_failed(
            task=task,
            vault_id=identity.vault_id,
            vault_name=vault_name,
            path=relative_path,
            related_path=None,
            operation=operation,
            stage=stage,
            before_exists=before_exists,
            before_hash=before_hash,
            before_snapshot_id=before_snapshot_id,
            error=exc,
        )
        if stage in UNCERTAIN_MUTATION_STAGES:
            raise VaultMutationRejected(
                "mutation_state_uncertain",
                (
                    f"{operation} for '{relative_path}' may have been applied, "
                    f"but vault-state recording failed during {stage}: {exc}"
                ),
            ) from exc
        raise
    return result


def _log_mutation_failed(
    *,
    task: Any,
    vault_id: str,
    vault_name: str,
    path: str,
    related_path: str | None,
    operation: str,
    stage: str,
    before_exists: bool,
    before_hash: str | None,
    before_snapshot_id: int | None,
    error: Exception,
) -> None:
    """Log an unexpected failure in the shared vault mutation path."""
    logger.add_sink("validation").warning(
        "vault_state_mutation_failed",
        data={
            "event": "vault_state_mutation_failed",
            "task_id": task.task_id if task is not None else None,
            "task_kind": task.kind if task is not None else None,
            "task_source": task.source if task is not None else None,
            "task_scope": task.scope if task is not None else None,
            "task_label": task.label if task is not None else None,
            "vault_id": vault_id,
            "vault_name": vault_name,
            "path": path,
            "related_path": related_path,
            "operation": operation,
            "stage": stage,
            "before_exists": before_exists,
            "before_hash": before_hash,
            "before_snapshot_id": before_snapshot_id,
            "error_type": type(error).__name__,
            "error": str(error),
        },
    )


def _directory_descendant_counts(path: Path) -> tuple[int, int]:
    """Count files and child directories without following directory symlinks."""
    file_count = 0
    directory_count = 0
    for _root, directories, files in os.walk(path):
        directory_count += len(directories)
        file_count += len(files)
    return file_count, directory_count


def _relative_to_vault(vault_root: Path, path: Path) -> str:
    return str(normalize_vault_relative_path(path.relative_to(vault_root)))


def _persist_or_log_mutation(
    *,
    service: VaultStateService,
    task: Any,
    result: RecordedMutationResult,
    vault_root: Path,
    operation_id: str,
    created_at: datetime,
    expires_at: datetime | None,
    warn_without_task: bool = True,
    target_kind: str = "file",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist a mutation under explicit or task-derived activity attribution."""
    context = _mutation_activity_context(task=task, result=result)
    if context is None:
        if not warn_without_task:
            return
        logger.add_sink("validation").warning(
            "vault_state_mutation_untracked",
            data={
                "event": "vault_state_mutation_untracked",
                "vault_id": result.vault_id,
                "vault_name": result.vault_name,
                "path": result.path,
                "related_path": result.related_path,
                "operation": result.operation,
                "reason": "missing_execution_task_context",
            },
        )
        return

    service.ensure_activity(
        context=context,
        vault_path=vault_root,
        vault_name=result.vault_name,
        created_at=created_at,
    )
    with service.SessionFactory() as session:
        activity = session.get(VaultActivity, context.activity_id)
        if activity is None:
            raise RuntimeError(
                f"Vault activity disappeared before mutation persistence: {context.activity_id}"
            )
        activity.updated_at = created_at
        if expires_at is not None and (
            activity.expires_at is None
            or _utc_datetime(expires_at) > _utc_datetime(activity.expires_at)
        ):
            activity.expires_at = expires_at
        session.add(
            VaultMutation(
                activity_id=context.activity_id,
                operation_id=operation_id,
                path=result.path,
                related_path=result.related_path,
                target_kind=target_kind,
                operation=result.operation,
                status="completed",
                event_sequence=result.event_sequence,
                before_exists=result.before_exists,
                before_hash=result.before_hash,
                before_snapshot_id=result.before_snapshot_id,
                after_exists=result.after_exists,
                after_hash=result.after_hash,
                after_snapshot_id=None,
                snapshot_ref=result.snapshot_ref,
                created_at=created_at,
                expires_at=expires_at,
                metadata_json=(
                    json.dumps(metadata, sort_keys=True) if metadata else None
                ),
            )
        )
        session.commit()

    logger.set_sinks(["validation"]).info(
        "vault_mutation_recorded",
        data={
            "event": "vault_mutation_recorded",
            "activity_id": context.activity_id,
            "activity_kind": context.kind,
            "activity_source": context.source,
            "task_id": context.task_id,
            "goal_id": context.goal_id,
            "step_id": context.step_id,
            "vault_id": result.vault_id,
            "vault_name": result.vault_name,
            "path": result.path,
            "related_path": result.related_path,
            "operation": result.operation,
            "before_exists": result.before_exists,
            "after_exists": result.after_exists,
            "before_hash": result.before_hash,
            "after_hash": result.after_hash,
            "before_snapshot_id": result.before_snapshot_id,
            "event_sequence": result.event_sequence,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "snapshot_ref": result.snapshot_ref,
        },
    )


def _persist_mutation_batch(
    *,
    service: VaultStateService,
    context: VaultActivityContext,
    results: tuple[RecordedMutationResult, ...],
    vault_root: Path,
    operation_id: str,
    created_at: datetime,
    expires_at: datetime | None,
    metadata: dict[str, Any] | None,
) -> None:
    """Persist one atomic multi-path mutation as a single logical operation."""
    with service.SessionFactory() as session:
        activity = session.get(VaultActivity, context.activity_id)
        if activity is None:
            raise RuntimeError(
                f"Vault activity disappeared before mutation persistence: {context.activity_id}"
            )
        activity.updated_at = created_at
        if expires_at is not None and (
            activity.expires_at is None
            or _utc_datetime(expires_at) > _utc_datetime(activity.expires_at)
        ):
            activity.expires_at = expires_at
        for result in results:
            session.add(
                VaultMutation(
                    activity_id=context.activity_id,
                    operation_id=operation_id,
                    path=result.path,
                    related_path=None,
                    target_kind="file",
                    operation=result.operation,
                    status="completed",
                    event_sequence=result.event_sequence,
                    before_exists=result.before_exists,
                    before_hash=result.before_hash,
                    before_snapshot_id=result.before_snapshot_id,
                    after_exists=result.after_exists,
                    after_hash=result.after_hash,
                    after_snapshot_id=None,
                    snapshot_ref=result.snapshot_ref,
                    created_at=created_at,
                    expires_at=expires_at,
                    metadata_json=(
                        json.dumps(metadata, sort_keys=True) if metadata else None
                    ),
                )
            )
        remove_favorite_paths(
            vault_root,
            [result.path for result in results if not result.after_exists],
            session=session,
        )
        session.commit()

    for result in results:
        logger.set_sinks(["validation"]).info(
            "vault_mutation_recorded",
            data={
                "event": "vault_mutation_recorded",
                "activity_id": context.activity_id,
                "activity_kind": context.kind,
                "activity_source": context.source,
                "task_id": context.task_id,
                "goal_id": context.goal_id,
                "step_id": context.step_id,
                "vault_id": result.vault_id,
                "vault_name": result.vault_name,
                "path": result.path,
                "related_path": None,
                "operation": result.operation,
                "before_exists": result.before_exists,
                "after_exists": result.after_exists,
                "before_hash": result.before_hash,
                "after_hash": result.after_hash,
                "before_snapshot_id": result.before_snapshot_id,
                "event_sequence": result.event_sequence,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "snapshot_ref": result.snapshot_ref,
            },
        )


def _mutation_activity_context(
    *,
    task: Any,
    result: RecordedMutationResult,
) -> VaultActivityContext | None:
    explicit = get_current_vault_activity()
    if explicit is not None:
        return explicit
    if task is None:
        return None
    goal_id, step_id = goal_context_from_metadata(getattr(task, "metadata", None))
    return VaultActivityContext(
        activity_id=task_activity_id(task.task_id, result.vault_id),
        kind=task.kind,
        source=task.source,
        scope=task.scope,
        label=task.label,
        task_id=task.task_id,
        goal_id=goal_id,
        step_id=step_id,
    )


def _snapshot_attribution(task: Any) -> SnapshotAttribution | None:
    explicit = get_current_vault_activity()
    if explicit is not None:
        return SnapshotAttribution(
            activity_id=explicit.activity_id,
            task_id=explicit.task_id,
            kind=explicit.kind,
            source=explicit.source,
            scope=explicit.scope,
            label=explicit.label,
            purpose="revision",
            snapshot_source="activity_mutation_before",
            scope_kind="activity",
            scope_id=explicit.activity_id,
        )
    if task is None:
        return None
    return SnapshotAttribution(
        activity_id=None,
        task_id=task.task_id,
        kind=task.kind,
        source=task.source,
        scope=task.scope,
        label=task.label,
        purpose="rollback",
        snapshot_source="task_mutation_before",
        scope_kind="task",
        scope_id=task.task_id,
    )


def _ensure_explicit_activity(
    *,
    service: VaultStateService,
    attribution: SnapshotAttribution,
    vault_root: Path,
    vault_name: str,
    created_at: datetime,
) -> None:
    if attribution.activity_id is None:
        return
    context = get_current_vault_activity()
    if context is None or context.activity_id != attribution.activity_id:
        raise RuntimeError(
            "Snapshot activity attribution no longer matches the active context"
        )
    service.ensure_activity(
        context=context,
        vault_path=vault_root,
        vault_name=vault_name,
        created_at=created_at,
    )


def _utc_datetime(value: datetime) -> datetime:
    """Normalize SQLite-naive and timezone-aware timestamps for comparison."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
