"""Confirmed explorer deletion through recorded file and directory mutations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.utils.hash import hash_file_bytes
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


@dataclass(frozen=True)
class DeletedVaultPath:
    path: str
    kind: Literal["file", "directory"]


def delete_vault_paths(
    *, vault_path: Path, sources: list[str], confirmed_sources: list[str]
) -> tuple[DeletedVaultPath, ...]:
    """Preflight a confirmed selection and record all removed states together."""
    if not sources or sources != confirmed_sources:
        raise VaultMutationRejected(
            "confirmation_required", "Confirm the complete selection before deleting."
        )
    root = vault_path.resolve()
    with vault_directory_mutation_lock(root):
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
            for name, target in candidates.items()
            if not any(parent in candidates for parent in _ancestors(name))
        }
        files: set[Path] = set()
        directories: set[Path] = set()
        results = []
        for name, target in selected.items():
            results.append(
                DeletedVaultPath(name, "directory" if target.is_dir() else "file")
            )
            if target.is_file():
                files.add(target)
                continue
            directories.add(target)
            for current, dirnames, filenames in os.walk(
                target, onerror=_raise_walk_error
            ):
                for child_name in dirnames + filenames:
                    child = Path(current) / child_name
                    _validate_target(child)
                    (directories if child.is_dir() else files).add(child)
        states = tuple(
            FileStateRestore(
                path=path.relative_to(root).as_posix(),
                expected_exists=True,
                expected_sha256=hash_file_bytes(path, length=None),
            )
            for path in sorted(files)
        )
        if states:
            restore_vault_file_states(
                vault_path=root, states=states, operation="delete"
            )
        for path in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            path.rmdir()
            try:
                record_vault_directory_mutation(
                    vault_path=root,
                    path=path.relative_to(root).as_posix(),
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
                        f"Directory recording failed and '{path.relative_to(root).as_posix()}' could not be restored: {compensation_error}",
                    ) from cause
                raise
        remove_favorite_paths(
            root, [path.relative_to(root).as_posix() for path in directories]
        )
        VaultStateService().refresh_vault(root, vault_name=root.name)
        return tuple(results)


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
