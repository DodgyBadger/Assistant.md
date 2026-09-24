"""
Storage helpers for writing rendered artifacts to vaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.ingestion.models import RenderOptions
from core.runtime.paths import get_data_root
from core.utils.hash import hash_bytes
from core.vault_state.file_mutations import (
    FileStateRestore,
    restore_file_states_atomically,
    write_vault_file,
    write_vault_file_bytes,
)


@dataclass(frozen=True)
class StoredArtifact:
    path: str
    sha256: str


def default_storage(rendered: list[dict], options: RenderOptions) -> list[str]:
    """Store rendered artifacts and return their vault-relative paths."""
    return [artifact.path for artifact in store_rendered_artifacts(rendered, options)]


def store_rendered_artifacts(
    rendered: list[dict], options: RenderOptions
) -> list[StoredArtifact]:
    """
    Write rendered artifacts to disk and return written paths (vault-relative).
    """
    if not options.vault:
        raise ValueError("RenderOptions.vault is required for storage")

    data_root = Path(get_data_root())
    vault_root = data_root / options.vault
    normalized: list[tuple[Path, bytes, bool]] = []
    seen: set[str] = set()
    for artifact in rendered:
        raw_rel_path = Path(artifact["path"])
        rel_path = Path(*[part for part in raw_rel_path.parts if part not in ("", ".")])
        relative = rel_path.as_posix()
        if relative in seen:
            raise ValueError(f"Duplicate rendered artifact path: {relative}")
        seen.add(relative)
        if (vault_root / rel_path).exists():
            raise FileExistsError(f"Import output already exists: {relative}")
        if "content_bytes" in artifact:
            content_bytes = artifact["content_bytes"]
            if not isinstance(content_bytes, bytes | bytearray):
                raise ValueError(
                    f"Binary artifact content_bytes must be bytes for {relative}"
                )
            normalized.append((rel_path, bytes(content_bytes), True))
        else:
            content = artifact["content"]
            if not isinstance(content, str):
                raise ValueError(f"Text artifact content must be text for {relative}")
            normalized.append((rel_path, content.encode("utf-8"), False))

    attempted: list[StoredArtifact] = []
    try:
        for rel_path, content, binary in normalized:
            relative = rel_path.as_posix()
            attempted.append(
                StoredArtifact(path=relative, sha256=hash_bytes(content, length=None))
            )
            if binary:
                write_vault_file_bytes(
                    vault_path=vault_root,
                    path=relative,
                    content=content,
                    warn_without_task=False,
                )
            else:
                write_vault_file(
                    vault_path=vault_root,
                    path=relative,
                    content=content.decode("utf-8"),
                    warn_without_task=False,
                )
    except Exception:
        remove_stored_artifacts(vault_root, attempted)
        raise
    return attempted


def remove_stored_artifacts(vault_root: Path, artifacts: list[StoredArtifact]) -> None:
    """Remove unchanged create-only artifacts while preserving modified files."""
    cleanup_errors: list[str] = []
    for artifact in reversed(artifacts):
        if not (vault_root / artifact.path).exists():
            continue
        try:
            restore_file_states_atomically(
                vault_path=vault_root,
                states=(
                    FileStateRestore(
                        path=artifact.path,
                        expected_exists=True,
                        expected_sha256=artifact.sha256,
                    ),
                ),
            )
        except Exception as exc:
            cleanup_errors.append(f"{artifact.path}: {exc}")
    if cleanup_errors:
        raise RuntimeError(
            "Import artifact cleanup was incomplete: " + "; ".join(cleanup_errors)
        )
