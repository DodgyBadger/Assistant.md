"""Confirmed explorer deletion through recorded file and directory mutations."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    get_current_execution_task,
    goal_context_from_metadata,
)
from core.utils.hash import hash_bytes, hash_file_bytes
from core.vault_state.activity import (
    VaultActivityContext,
    get_current_vault_activity,
    use_vault_activity,
)
from core.vault_state.favorites import remove_favorite_paths
from core.vault_state.file_mutations import (
    FileStateRestore,
    VaultMutationRejected,
    record_vault_directory_mutation,
    resolve_vault_deletion_target,
    restore_vault_file_states,
    vault_directory_mutation_lock,
)
from core.vault_state.identity import VAULT_METADATA_RELATIVE_PATH
from core.vault_state.service import VaultStateService

logger = UnifiedLogger(tag="vault-deletion")


@dataclass(frozen=True)
class DeletedVaultPath:
    path: str
    kind: Literal["file", "directory"]


@dataclass(frozen=True)
class VaultDeletionSelection:
    targets: tuple[DeletedVaultPath, ...]
    files: tuple[FileStateRestore, ...]
    directories: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        content = {
            "targets": [(target.path, target.kind) for target in self.targets],
            "files": [(state.path, state.expected_sha256) for state in self.files],
            "directories": self.directories,
        }
        return hash_bytes(json.dumps(content, sort_keys=True).encode(), length=None)

    def target_info(self) -> list[dict[str, str]]:
        return [{"path": target.path, "kind": target.kind} for target in self.targets]


class VaultDeletionFailed(VaultMutationRejected):
    def __init__(self, activity_id: str, recovery_status: str) -> None:
        super().__init__(
            "delete_failed",
            f"Deletion failed; recovery status: {recovery_status}. Inspect Vault Activity {activity_id} before retrying.",
        )
        self.activity_id = activity_id
        self.recovery_status = recovery_status


def deletion_sources(*, path: str = "", paths: list[str] | None = None) -> list[str]:
    """Validate the tool's single-or-multiple target input without mutating state."""
    if path and paths is not None:
        raise VaultMutationRejected(
            "invalid_path", "Supply either path or paths, not both."
        )
    sources = [path] if paths is None else paths
    if (
        not isinstance(sources, list)
        or not sources
        or any(not isinstance(source, str) or not source.strip() for source in sources)
    ):
        raise VaultMutationRejected(
            "invalid_path",
            "Supply a path or a nonempty paths list of vault-relative targets.",
        )
    return sources


def prepare_vault_deletion(
    *, vault_path: Path, sources: list[str]
) -> VaultDeletionSelection:
    root = vault_path.resolve()
    with vault_directory_mutation_lock(root):
        return _prepare_selection(root, deletion_sources(paths=sources))


def _prepare_selection(root: Path, sources: list[str]) -> VaultDeletionSelection:
    candidates: dict[str, Path] = {}
    for source in sources:
        target = resolve_vault_deletion_target(vault_path=root, path=source)
        metadata = (root / VAULT_METADATA_RELATIVE_PATH).resolve()
        if target == metadata or target in metadata.parents:
            raise VaultMutationRejected(
                "protected_vault_identity",
                "Vault identity metadata and its containing folders cannot be deleted.",
            )
        _validate_target(root / source)
        candidates[target.relative_to(root).as_posix()] = target
    selected = {
        name: target
        for name, target in sorted(candidates.items())
        if not any(parent in candidates for parent in _ancestors(name))
    }
    files: set[Path] = set()
    directories: set[Path] = set()
    targets = []
    for name, target in selected.items():
        targets.append(
            DeletedVaultPath(name, "directory" if target.is_dir() else "file")
        )
        if target.is_file():
            files.add(target)
            continue
        directories.add(target)
        for current, dirnames, filenames in os.walk(target, onerror=_raise_walk_error):
            for child_name in dirnames + filenames:
                child = Path(current) / child_name
                _validate_target(child)
                (directories if child.is_dir() else files).add(child)
    return VaultDeletionSelection(
        targets=tuple(targets),
        files=tuple(
            FileStateRestore(
                path=path.relative_to(root).as_posix(),
                expected_exists=True,
                expected_sha256=hash_file_bytes(path, length=None),
            )
            for path in sorted(files)
        ),
        directories=tuple(
            sorted(path.relative_to(root).as_posix() for path in directories)
        ),
    )


@contextmanager
def vault_deletion_activity() -> Iterator[VaultActivityContext]:
    """Give tool deletion its own recoverable unit while preserving task provenance."""
    task = get_current_execution_task()
    goal_id, step_id = goal_context_from_metadata(task.metadata if task else None)
    context = VaultActivityContext(
        activity_id=f"activity_{uuid.uuid4().hex}",
        kind=task.kind if task else "tool",
        source=task.source if task else "tool",
        scope=task.scope if task else None,
        label=task.label if task else "Delete selected vault paths",
        task_id=task.task_id if task else None,
        goal_id=goal_id,
        step_id=step_id,
    )
    with use_vault_activity(context):
        try:
            yield context
        except Exception:
            VaultStateService().finish_activity(
                activity_id=context.activity_id, status="failed"
            )
            raise
        else:
            VaultStateService().finish_activity(
                activity_id=context.activity_id, status="completed"
            )


def delete_vault_paths(
    *,
    vault_path: Path,
    sources: list[str],
    confirmed_sources: list[str] | None = None,
    expected_fingerprint: str | None = None,
) -> tuple[DeletedVaultPath, ...]:
    """Preflight and execute one recoverable recursive selection under its lock."""
    if confirmed_sources is not None and (not sources or sources != confirmed_sources):
        raise VaultMutationRejected(
            "confirmation_required", "Confirm the complete selection before deleting."
        )
    root = vault_path.resolve()
    with vault_directory_mutation_lock(root):
        selection = _prepare_selection(root, deletion_sources(paths=sources))
        if (
            expected_fingerprint is not None
            and selection.fingerprint != expected_fingerprint
        ):
            raise VaultMutationRejected(
                "review_target_changed",
                "The reviewed deletion selection changed. Request a new review before deleting.",
            )
        context = get_current_vault_activity()
        if context is None:
            raise VaultMutationRejected(
                "missing_activity_context",
                "Deletion requires a recorded vault activity.",
            )
        try:
            _execute_selection(root, selection)
        except Exception as cause:
            service = VaultStateService()
            service.finish_activity(activity_id=context.activity_id, status="failed")
            restored = _matches_selection(root, selection)
            if not restored:
                restored = _recover_selection(root, context.activity_id, selection)
            recovery_status = "restored" if restored else "partial_or_uncertain"
            logger.error(
                "Vault deletion failed",
                data={
                    "event": "vault_delete_failed",
                    "status": "failed",
                    "vault_name": root.name,
                    "activity_id": context.activity_id,
                    "recovery_status": recovery_status,
                    "error_type": type(cause).__name__,
                    "issue": f"vault_delete_failed:{context.activity_id}",
                    "error": "Inspect the linked Vault Activity before retrying deletion.",
                },
            )
            raise VaultDeletionFailed(context.activity_id, recovery_status) from cause
        return selection.targets


def _execute_selection(root: Path, selection: VaultDeletionSelection) -> None:
    if selection.files:
        restore_vault_file_states(
            vault_path=root, states=selection.files, operation="delete"
        )
    for name in sorted(
        selection.directories, key=lambda item: len(Path(item).parts), reverse=True
    ):
        path = root / name
        path.rmdir()
        try:
            record_vault_directory_mutation(
                vault_path=root,
                path=name,
                operation="delete",
                before_exists=True,
                after_exists=False,
                metadata={"directory_state": True},
            )
        except Exception as cause:
            try:
                path.mkdir(exist_ok=True)
            except OSError as compensation_error:
                raise VaultMutationRejected(
                    "mutation_state_uncertain",
                    "Directory recording and immediate restoration failed.",
                ) from compensation_error
            raise cause
    remove_favorite_paths(root, list(selection.directories))
    VaultStateService().refresh_vault(root, vault_name=root.name)


def _matches_selection(root: Path, selection: VaultDeletionSelection) -> bool:
    try:
        actual = _prepare_selection(root, [target.path for target in selection.targets])
    except (VaultMutationRejected, ValueError, OSError):
        return False
    return actual.fingerprint == selection.fingerprint


def _recover_selection(
    root: Path, activity_id: str, selection: VaultDeletionSelection
) -> bool:
    from core.vault_state.activity_rollback import (
        execute_activity_rollback,
        preview_activity_rollback,
    )

    try:
        plan = preview_activity_rollback(vault_path=root, activity_id=activity_id)
        if not plan.can_rollback:
            return False
        execute_activity_rollback(
            vault_path=root,
            activity_id=activity_id,
            expected_states=[
                (path.path, path.expected_exists, path.expected_sha256)
                for path in plan.paths
            ],
        )
    except Exception as exc:
        logger.error(
            "Vault deletion recovery did not complete",
            data={
                "event": "vault_delete_recovery_failed",
                "status": "failed",
                "activity_id": activity_id,
                "vault_name": root.name,
                "error_type": type(exc).__name__,
                "issue": f"vault_delete_recovery_failed:{activity_id}",
                "error": "Retained mutations may require manual recovery through Vault Activity.",
            },
        )
        return False
    return _matches_selection(root, selection)


def _ancestors(path: str) -> tuple[str, ...]:
    return tuple(
        parent.as_posix() for parent in Path(path).parents if parent != Path(".")
    )


def _validate_target(path: Path) -> None:
    if path.is_symlink():
        raise VaultMutationRejected(
            "symlink_target", f"Cannot recursively delete symbolic link: {path.name}"
        )
    if not path.exists():
        raise VaultMutationRejected(
            "file_not_found", f"Delete target does not exist: {path.name}"
        )
    if not path.is_file() and not path.is_dir():
        raise VaultMutationRejected(
            "invalid_target", f"Unsupported delete target: {path.name}"
        )


def _raise_walk_error(error: OSError) -> None:
    raise error
