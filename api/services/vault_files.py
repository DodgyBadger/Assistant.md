"""Vault file, Explorer, revision, upload, and reference API services."""

import mimetypes
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from core.constants import ASSISTANTMD_ROOT_DIR
from core.runtime.state import get_runtime_context
from core.vault_state.activity import VaultActivityContext, use_vault_activity
from core.vault_state.file_mutations import (
    VaultMutationRejected,
    restore_vault_file,
    vault_directory_mutation_lock,
    write_vault_file,
    write_vault_file_bytes,
)
from core.vault_state.file_operations import (
    VaultFileOperationRejected,
    VaultFileOperationResult,
    delete_vault_path_operation,
    make_vault_directory_operation,
    move_vault_directory_operation,
    move_vault_path_operation,
    replace_full_text_content,
)
from core.vault_state.pathing import (
    VaultRootResolutionError,
    normalize_vault_relative_path,
    resolve_configured_vault_root,
    resolve_vault_relative_path,
)
from core.vault_state.service import VaultStateService

from ..exceptions import APIException
from ..models import (
    VaultBatchMoveResponse,
    VaultDirectoryInfo,
    VaultDirectoryListResponse,
    VaultFileReferenceInfo,
    VaultFileReferenceListResponse,
    VaultFileResponse,
    VaultFileRevisionInfo,
    VaultFileRevisionResponse,
    VaultFileRevisionRestoreResponse,
    VaultPathMutationResponse,
    VaultPathResolutionInfo,
    VaultPathResolveResponse,
)
from .chat_sessions import (
    _normalize_workspace_path,
)
from .shared import logger
from .workflows import (
    _sha256_text,
)

_VAULT_FILE_REFERENCE_LIMIT = 100
_VAULT_FILE_READ_MAX_BYTES = 2 * 1024 * 1024
_NON_TEXT_MEDIA_TYPE_PREFIXES = (
    "application/vnd.ms-",
    "application/vnd.openxmlformats-officedocument.",
    "audio/",
    "font/",
    "image/",
    "video/",
)
_NON_TEXT_MEDIA_TYPES = {
    "application/epub+zip",
    "application/gzip",
    "application/octet-stream",
    "application/pdf",
    "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.text",
    "application/x-7z-compressed",
    "application/x-bzip2",
    "application/x-rar-compressed",
    "application/x-tar",
    "application/zip",
}


def resolve_vault_root(vault_name: str) -> Path:
    """Return an existing vault root for API file operations."""
    runtime = get_runtime_context()
    try:
        return resolve_configured_vault_root(
            data_root=runtime.config.data_root,
            vault_name=vault_name,
        )
    except VaultRootResolutionError as exc:
        status_code = 404 if exc.code == "vault_not_found" else 400
        error_type = {
            "invalid_vault_name": "InvalidVaultName",
            "vault_root_escapes_data_root": "VaultRootEscapesDataRoot",
            "vault_not_found": "VaultNotFound",
        }.get(exc.code, "InvalidVaultName")
        raise APIException(
            status_code=status_code,
            error_type=error_type,
            message=str(exc),
            details={"vault_name": exc.vault_name},
        ) from exc


def _normalize_vault_file_path(path: str | None) -> str:
    raw_path = str(path or "").strip()
    slash_normalized = raw_path.replace("\\", "/")
    has_drive_prefix = (
        len(slash_normalized) >= 3
        and slash_normalized[0].isalpha()
        and slash_normalized[1:3] == ":/"
    )
    path_parts = slash_normalized.split("/")
    if (
        not raw_path
        or raw_path.startswith(("/", "\\"))
        or has_drive_prefix
        or any(part in {".", ".."} for part in path_parts)
        or any(ord(character) < 32 for character in raw_path)
        or len(raw_path) > 1000
    ):
        raise APIException(
            status_code=400,
            error_type="InvalidVaultFilePath",
            message="A safe vault-relative file path is required.",
            details={"path": raw_path[:100]},
        )
    normalized = normalize_vault_relative_path(raw_path)
    return normalized


def _resolve_vault_file_path(
    vault_name: str, path: str | None
) -> tuple[Path, str, Path]:
    """Resolve a vault-relative file path under an existing vault."""
    vault_root = resolve_vault_root(vault_name)
    normalized = _normalize_vault_file_path(path)
    try:
        resolved = resolve_vault_relative_path(
            vault_path=vault_root,
            path=normalized,
            markdown_only=False,
        )
    except ValueError as exc:
        raise APIException(
            status_code=400,
            error_type="InvalidVaultFilePath",
            message=str(exc),
            details={"path": path, "vault_name": vault_name},
        ) from exc
    return vault_root, normalized, resolved


def _datetime_from_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None


def _vault_file_response(
    *,
    vault_name: str,
    path: str,
    full_path: Path,
    content: str,
    message: str | None = None,
) -> VaultFileResponse:
    encoded = content.encode("utf-8")
    return VaultFileResponse(
        vault_name=vault_name,
        path=path,
        name=full_path.name,
        content=content,
        sha256=_sha256_text(content),
        size_bytes=len(encoded),
        modified_at=_datetime_from_mtime(full_path),
        media_type=mimetypes.guess_type(path)[0] or "text/plain",
        message=message,
    )


def get_vault_file(vault_name: str, path: str) -> VaultFileResponse:
    """Return editable text content for one vault file."""
    _, normalized, full_path = _resolve_vault_file_path(vault_name, path)
    if not full_path.exists():
        raise APIException(
            status_code=404,
            error_type="VaultFileNotFound",
            message=f"Vault file not found: {normalized}",
            details={"path": normalized, "vault_name": vault_name},
        )
    if not full_path.is_file():
        raise APIException(
            status_code=400,
            error_type="VaultPathNotFile",
            message=f"Vault path is not a file: {normalized}",
            details={"path": normalized, "vault_name": vault_name},
        )
    content = _read_editable_vault_text(
        full_path=full_path,
        path=normalized,
        vault_name=vault_name,
    )
    return _vault_file_response(
        vault_name=vault_name,
        path=normalized,
        full_path=full_path,
        content=content,
    )


def get_vault_file_revisions(
    *,
    vault_name: str,
    path: str,
    limit: int = 50,
) -> VaultFileRevisionResponse:
    """Return retained pre-mutation states for one exact vault file path."""
    _, normalized, _ = _resolve_vault_file_path(vault_name, path)
    revisions = VaultStateService().list_file_revisions(
        vault_name=vault_name,
        path=normalized,
        limit=limit,
    )
    return VaultFileRevisionResponse(
        vault_name=vault_name,
        path=normalized,
        revisions=[
            VaultFileRevisionInfo(
                snapshot_id=revision.snapshot_id,
                activity_id=revision.activity_id,
                activity_kind=revision.activity_kind,
                activity_source=revision.activity_source,
                activity_label=revision.activity_label,
                task_id=revision.task_id,
                path=revision.path,
                operation=revision.operation,
                exists=revision.exists,
                content_hash=revision.content_hash,
                snapshot_available=revision.snapshot_available,
                created_at=revision.created_at,
                expires_at=revision.expires_at,
            )
            for revision in revisions
        ],
    )


def restore_vault_file_revision(
    *,
    vault_name: str,
    snapshot_id: int,
    expected_sha256: str | None,
) -> VaultFileRevisionRestoreResponse:
    """Restore one retained exact-path revision as a new Explorer mutation."""
    service = VaultStateService()
    revision = service.get_file_revision(
        vault_name=vault_name,
        snapshot_id=snapshot_id,
    )
    if revision is None:
        raise APIException(
            status_code=404,
            error_type="VaultFileRevisionNotFound",
            message=f"Vault file revision not found or no longer retained: {snapshot_id}",
            details={"snapshot_id": snapshot_id, "vault_name": vault_name},
        )

    vault_root, normalized, full_path = _resolve_vault_file_path(
        vault_name,
        revision.path,
    )
    if full_path.exists() and not full_path.is_file():
        raise APIException(
            status_code=409,
            error_type="VaultFileConflict",
            message=f"Cannot restore over a directory: {normalized}",
            details={"path": normalized, "vault_name": vault_name},
        )

    if not revision.exists and not full_path.exists() and expected_sha256 is None:
        return VaultFileRevisionRestoreResponse(
            vault_name=vault_name,
            path=normalized,
            snapshot_id=snapshot_id,
            exists=False,
            sha256=None,
            message=f"{normalized} is already absent.",
        )

    content: bytes | None = None
    if revision.exists:
        snapshot = service.resolve_snapshot_file(snapshot_id)
        if snapshot is None:
            raise APIException(
                status_code=404,
                error_type="VaultFileRevisionNotFound",
                message=f"Revision content is no longer retained: {snapshot_id}",
                details={"snapshot_id": snapshot_id, "vault_name": vault_name},
            )
        content = snapshot.path.read_bytes()

    try:
        with _explorer_activity(label=f"Restore {normalized}"):
            mutation = restore_vault_file(
                vault_path=vault_root,
                path=normalized,
                content=content,
                expected_sha256=expected_sha256,
            )
    except VaultMutationRejected as exc:
        if exc.code in {"file_conflict", "file_not_found"}:
            raise APIException(
                status_code=409,
                error_type="VaultFileConflict",
                message="The file changed since its revision history was opened. Refresh and retry.",
                details={"path": normalized, "vault_name": vault_name},
            ) from exc
        raise

    return VaultFileRevisionRestoreResponse(
        vault_name=vault_name,
        path=normalized,
        snapshot_id=snapshot_id,
        exists=mutation.after_exists,
        sha256=mutation.after_hash,
        message=(
            f"Restored {normalized}."
            if mutation.after_exists
            else f"Restored the earlier absent state for {normalized}."
        ),
    )


def update_vault_file(
    *,
    vault_name: str,
    path: str,
    content: str,
    expected_sha256: str | None = None,
    create_if_missing: bool = False,
) -> VaultFileResponse:
    """Replace one vault text file after an optional content-hash check."""
    vault_root, normalized, full_path = _resolve_vault_file_path(vault_name, path)
    if not full_path.exists() or not full_path.is_file():
        if create_if_missing and not full_path.exists():
            with _explorer_activity(
                label=f"Create {normalized}",
            ):
                write_vault_file(
                    vault_path=vault_root,
                    path=normalized,
                    content=content,
                    fail_if_exists=True,
                    markdown_only=False,
                )
            return _vault_file_response(
                vault_name=vault_name,
                path=normalized,
                full_path=full_path,
                content=content,
                message=f"Created {normalized}.",
            )
        raise APIException(
            status_code=404,
            error_type="VaultFileNotFound",
            message=f"Vault file not found: {normalized}",
            details={"path": normalized, "vault_name": vault_name},
        )
    _read_editable_vault_text(
        full_path=full_path,
        path=normalized,
        vault_name=vault_name,
    )
    try:
        with _explorer_activity(
            label=f"Edit {normalized}",
        ):
            replace_full_text_content(
                vault_path=vault_root,
                path=normalized,
                content=content,
                operation="update_vault_file",
                expected_sha256=expected_sha256,
                markdown_only=False,
            )
    except VaultFileOperationRejected as exc:
        if exc.code == "file_not_text":
            raise APIException(
                status_code=415,
                error_type="VaultFileNotText",
                message=f"Vault file is not UTF-8 text: {normalized}",
                details={"path": normalized, "vault_name": vault_name},
            ) from exc
        if exc.code == "file_conflict":
            raise APIException(
                status_code=409,
                error_type="VaultFileConflict",
                message="Vault file changed since it was opened. Refresh and retry.",
                details={
                    "path": normalized,
                    "vault_name": vault_name,
                    "expected_sha256": exc.details.get("expected_sha256"),
                    "current_sha256": exc.details.get("actual_sha256"),
                },
            ) from exc
        raise APIException(
            status_code=400,
            error_type=exc.code,
            message=str(exc),
            details={"path": normalized, "vault_name": vault_name, **exc.details},
        ) from exc
    return _vault_file_response(
        vault_name=vault_name,
        path=normalized,
        full_path=full_path,
        content=content,
        message=f"Saved {normalized}.",
    )


def _read_editable_vault_text(*, full_path: Path, path: str, vault_name: str) -> str:
    """Return UTF-8 text or reject content that should not enter the inline editor."""
    try:
        size_bytes = full_path.stat().st_size
    except OSError as exc:
        raise APIException(
            status_code=500,
            error_type="VaultFileStatFailed",
            message=f"Failed to inspect vault file: {path}",
            details={"path": path, "vault_name": vault_name},
        ) from exc
    if size_bytes > _VAULT_FILE_READ_MAX_BYTES:
        raise APIException(
            status_code=413,
            error_type="VaultFileTooLarge",
            message=f"Vault file is too large for inline editing: {path}",
            details={
                "path": path,
                "vault_name": vault_name,
                "size_bytes": size_bytes,
                "max_bytes": _VAULT_FILE_READ_MAX_BYTES,
            },
        )
    media_type = (mimetypes.guess_type(path)[0] or "").lower()
    if media_type in _NON_TEXT_MEDIA_TYPES or media_type.startswith(
        _NON_TEXT_MEDIA_TYPE_PREFIXES
    ):
        raise _vault_file_not_text_error(
            path=path, vault_name=vault_name, media_type=media_type
        )
    try:
        raw = full_path.read_bytes()
        content = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _vault_file_not_text_error(
            path=path, vault_name=vault_name, media_type=media_type
        ) from exc
    if b"\x00" in raw or any(
        byte < 32 and byte not in {8, 9, 10, 12, 13} for byte in raw
    ):
        raise _vault_file_not_text_error(
            path=path, vault_name=vault_name, media_type=media_type
        )
    return content


def _vault_file_not_text_error(
    *, path: str, vault_name: str, media_type: str
) -> APIException:
    return APIException(
        status_code=415,
        error_type="VaultFileNotText",
        message=f"Vault file is not editable as plain text: {path}",
        details={"path": path, "vault_name": vault_name, "media_type": media_type},
    )


def _resolve_existing_vault_directory(
    *, vault_name: str, path: str | None
) -> tuple[str, Path]:
    """Return a normalized path and existing directory for picker browsing."""
    normalized_path = _normalize_workspace_path(path)
    vault_root = resolve_vault_root(vault_name)
    resolved = (
        (vault_root / normalized_path).resolve() if normalized_path else vault_root
    )
    try:
        resolved.relative_to(vault_root)
    except ValueError as exc:
        raise APIException(
            status_code=400,
            error_type="InvalidWorkspacePath",
            message="Workspace path escapes the vault.",
            details={"path": path, "vault_name": vault_name},
        ) from exc
    if not resolved.exists():
        raise APIException(
            status_code=404,
            error_type="WorkspaceNotFound",
            message=f"Workspace directory not found: {normalized_path}",
            details={"path": normalized_path, "vault_name": vault_name},
        )
    if not resolved.is_dir():
        raise APIException(
            status_code=400,
            error_type="WorkspaceNotDirectory",
            message=f"Workspace path is not a directory: {normalized_path}",
            details={"path": normalized_path, "vault_name": vault_name},
        )
    return normalized_path, resolved


def list_vault_directories(
    vault_name: str, path: str | None = None
) -> VaultDirectoryListResponse:
    """Return child directories for one vault-relative path."""
    base_path, base_dir = _resolve_existing_vault_directory(
        vault_name=vault_name, path=path
    )
    vault_root = resolve_vault_root(vault_name)
    directories: list[VaultDirectoryInfo] = []
    for child in sorted(base_dir.iterdir(), key=lambda item: item.name.lower()):
        if not _is_workspace_picker_directory(child):
            continue
        try:
            relative = child.resolve().relative_to(vault_root).as_posix()
        except ValueError:
            continue
        has_children = any(
            _is_workspace_picker_directory(grandchild) for grandchild in child.iterdir()
        )
        directories.append(
            VaultDirectoryInfo(
                name=child.name,
                path=relative,
                has_children=has_children,
            )
        )
    return VaultDirectoryListResponse(path=base_path, directories=directories)


def _is_workspace_picker_directory(path: Path) -> bool:
    """Return whether a directory should appear in the workspace picker."""
    return (
        path.is_dir()
        and not path.name.startswith(".")
        and path.name != ASSISTANTMD_ROOT_DIR
    )


def list_vault_file_references(
    *,
    vault_name: str,
    path: str | None = None,
    workspace_path: str | None = None,
    query: str | None = None,
    scope: str = "workspace",
    limit: int = _VAULT_FILE_REFERENCE_LIMIT,
    offset: int = 0,
) -> VaultFileReferenceListResponse:
    """Return file/folder candidates for chat reference insertion."""
    vault_root = resolve_vault_root(vault_name)
    normalized_workspace = _normalize_workspace_path(workspace_path)
    normalized_scope: Literal["workspace", "vault"] = (
        "vault" if scope == "vault" else "workspace"
    )
    normalized_query = (query or "").strip().lower()
    bounded_limit = min(
        max(int(limit or _VAULT_FILE_REFERENCE_LIMIT), 1), _VAULT_FILE_REFERENCE_LIMIT
    )
    bounded_offset = max(int(offset or 0), 0)

    if normalized_query:
        requested_search_path = _normalize_workspace_path(path)
        base_relative = requested_search_path or (
            normalized_workspace if normalized_scope == "workspace" else ""
        )
        base_dir = resolve_vault_relative_path(
            vault_path=vault_root, path=base_relative
        )
        if not base_dir.exists() or not base_dir.is_dir():
            base_relative = ""
            base_dir = vault_root
        items, truncated = _search_vault_file_references(
            vault_root=vault_root,
            base_dir=base_dir,
            workspace_path=normalized_workspace,
            query=normalized_query,
            limit=bounded_limit,
        )
        return VaultFileReferenceListResponse(
            vault_name=vault_name,
            path=base_relative,
            workspace_path=normalized_workspace,
            query=normalized_query,
            scope=normalized_scope,
            items=items,
            truncated=truncated,
            next_offset=None,
        )

    base_relative = _normalize_workspace_path(path)
    if not base_relative and normalized_scope == "workspace":
        base_relative = normalized_workspace
    base_path, base_dir = _resolve_existing_vault_directory(
        vault_name=vault_name, path=base_relative
    )
    items, truncated = _list_vault_file_reference_children(
        vault_root=vault_root,
        base_dir=base_dir,
        workspace_path=normalized_workspace,
        limit=bounded_limit,
        offset=bounded_offset,
    )
    return VaultFileReferenceListResponse(
        vault_name=vault_name,
        path=base_path,
        workspace_path=normalized_workspace,
        query="",
        scope=normalized_scope,
        truncated=truncated,
        next_offset=bounded_offset + len(items) if truncated else None,
        items=items,
    )


def resolve_vault_path_references(
    *,
    vault_name: str,
    paths: list[str],
    workspace_path: str | None = None,
) -> VaultPathResolveResponse:
    """Resolve rendered chat path candidates without guessing recursively."""
    vault_root = resolve_vault_root(vault_name)
    normalized_workspace = _normalize_workspace_path(workspace_path)
    workspace_root: Path | None = None
    if normalized_workspace:
        candidate_workspace = resolve_vault_relative_path(
            vault_path=vault_root,
            path=normalized_workspace,
        )
        if candidate_workspace.is_dir():
            workspace_root = candidate_workspace

    items: list[VaultPathResolutionInfo] = []
    seen: set[str] = set()
    for raw_path in paths:
        requested_path = _normalize_chat_reference_candidate(raw_path)
        if not requested_path or requested_path in seen:
            continue
        seen.add(requested_path)

        resolved_item = None
        if "/" not in requested_path and workspace_root is not None:
            workspace_candidate = resolve_vault_relative_path(
                vault_path=workspace_root,
                path=requested_path,
            )
            resolved_item = _resolved_chat_path_item(
                vault_root=vault_root,
                requested_path=requested_path,
                candidate=workspace_candidate,
                source="workspace",
            )
        if resolved_item is None:
            vault_candidate = resolve_vault_relative_path(
                vault_path=vault_root,
                path=requested_path,
            )
            resolved_item = _resolved_chat_path_item(
                vault_root=vault_root,
                requested_path=requested_path,
                candidate=vault_candidate,
                source="vault",
            )
        items.append(
            resolved_item
            or VaultPathResolutionInfo(
                requested_path=requested_path,
                path=requested_path,
                kind="missing",
                source="missing",
            )
        )

    return VaultPathResolveResponse(
        vault_name=vault_name,
        workspace_path=normalized_workspace,
        items=items,
    )


def mutate_vault_path(
    *,
    vault_name: str,
    operation: str,
    path: str,
    destination: str = "",
    content: str = "",
) -> VaultPathMutationResponse:
    """Apply one direct explorer mutation through shared vault operations."""
    vault_root, normalized, full_path = _resolve_vault_file_path(vault_name, path)
    with _explorer_activity(
        label=f"{operation.replace('_', ' ').title()} {normalized}",
    ):
        return _mutate_vault_path_attributed(
            vault_name=vault_name,
            vault_root=vault_root,
            normalized=normalized,
            full_path=full_path,
            operation=operation,
            destination=destination,
            content=content,
        )


def move_vault_paths_batch(
    *,
    vault_name: str,
    sources: list[str],
    destination: str,
) -> VaultBatchMoveResponse:
    """Move several top-level selections as one preflighted Explorer activity."""
    if len(sources) < 2:
        raise APIException(
            status_code=400,
            error_type="InvalidVaultBatchMove",
            message="Batch move requires at least two source paths.",
        )
    vault_root = resolve_vault_root(vault_name)
    normalized_sources = [_normalize_vault_file_path(source) for source in sources]
    if len(set(normalized_sources)) != len(normalized_sources):
        raise APIException(
            status_code=400,
            error_type="DuplicateVaultBatchSource",
            message="Batch move source paths must be unique.",
        )
    for source in normalized_sources:
        if any(
            other != source and other.startswith(f"{source}/")
            for other in normalized_sources
        ):
            raise APIException(
                status_code=400,
                error_type="OverlappingVaultBatchSources",
                message="A selected folder contains another selected source.",
            )
    normalized_destination = _normalize_batch_destination(destination)
    destination_root = (
        resolve_vault_relative_path(
            vault_path=vault_root,
            path=normalized_destination,
            markdown_only=False,
        )
        if normalized_destination
        else vault_root
    )
    resolved_sources = [
        resolve_vault_relative_path(
            vault_path=vault_root,
            path=source,
            markdown_only=False,
        )
        for source in normalized_sources
    ]
    targets = [destination_root / source.name for source in resolved_sources]

    with vault_directory_mutation_lock(
        vault_root, *resolved_sources, destination_root, *targets
    ):
        _preflight_batch_move(
            normalized_sources=normalized_sources,
            resolved_sources=resolved_sources,
            destination_root=destination_root,
            targets=targets,
            vault_root=vault_root,
        )
        completed: list[VaultPathMutationResponse] = []
        with _explorer_activity(label=f"Move {len(normalized_sources)} items"):
            try:
                for source, source_path, target in zip(
                    normalized_sources,
                    resolved_sources,
                    targets,
                    strict=True,
                ):
                    target_relative = target.relative_to(vault_root).as_posix()
                    completed.append(
                        _mutate_vault_path_attributed(
                            vault_name=vault_name,
                            vault_root=vault_root,
                            normalized=source,
                            full_path=source_path,
                            operation="move",
                            destination=target_relative,
                            content="",
                        )
                    )
            except Exception as exc:
                compensation_errors: list[str] = []
                for result in reversed(completed):
                    try:
                        target_path = resolve_vault_relative_path(
                            vault_path=vault_root,
                            path=result.destination,
                            markdown_only=False,
                        )
                        _mutate_vault_path_attributed(
                            vault_name=vault_name,
                            vault_root=vault_root,
                            normalized=result.destination,
                            full_path=target_path,
                            operation="move",
                            destination=result.path,
                            content="",
                        )
                    except Exception as compensation_error:
                        compensation_errors.append(str(compensation_error))
                if compensation_errors:
                    raise APIException(
                        status_code=500,
                        error_type="VaultBatchMoveStateUncertain",
                        message="Batch move failed and could not be fully compensated.",
                        details={"compensation_errors": compensation_errors},
                    ) from exc
                raise

    return VaultBatchMoveResponse(
        destination=normalized_destination,
        results=completed,
        message=f"Moved {len(completed)} items.",
    )


def _normalize_batch_destination(path: str) -> str:
    raw_path = str(path or "").strip()
    if not raw_path:
        return ""
    return _normalize_vault_file_path(raw_path)


def _preflight_batch_move(
    *,
    normalized_sources: list[str],
    resolved_sources: list[Path],
    destination_root: Path,
    targets: list[Path],
    vault_root: Path,
) -> None:
    if not destination_root.exists() or not destination_root.is_dir():
        raise APIException(
            status_code=400,
            error_type="VaultBatchDestinationNotDirectory",
            message="Batch move destination must be an existing folder.",
        )
    if len(set(targets)) != len(targets):
        raise APIException(
            status_code=409,
            error_type="VaultBatchTargetCollision",
            message="Two selected items would have the same destination path.",
        )
    for source, source_path, target in zip(
        normalized_sources, resolved_sources, targets, strict=True
    ):
        if not source_path.exists():
            raise APIException(
                status_code=404,
                error_type="VaultPathNotFound",
                message=f"Vault path not found: {source}",
            )
        if target == source_path:
            raise APIException(
                status_code=409,
                error_type="VaultBatchSourceEqualsDestination",
                message=f"{source} is already in the selected destination.",
            )
        if source_path.is_dir() and source_path in target.parents:
            raise APIException(
                status_code=400,
                error_type="VaultBatchDestinationInsideSource",
                message=f"Cannot move {source} inside itself.",
            )
        if target.exists():
            target_relative = target.relative_to(vault_root).as_posix()
            raise APIException(
                status_code=409,
                error_type="VaultPathExists",
                message=f"Vault path already exists: {target_relative}",
            )


def upload_vault_file(
    *,
    vault_name: str,
    path: str,
    content: bytes,
) -> VaultPathMutationResponse:
    """Create one binary-safe vault file from an Explorer upload."""
    vault_root, normalized, _ = resolve_vault_upload_target(
        vault_name=vault_name,
        path=path,
    )

    with _explorer_activity(label=f"Upload {normalized}"):
        try:
            mutation = write_vault_file_bytes(
                vault_path=vault_root,
                path=normalized,
                content=content,
                fail_if_exists=True,
                warn_without_task=False,
            )
        except VaultMutationRejected as exc:
            raise _vault_path_mutation_error(
                exc,
                vault_name=vault_name,
                path=normalized,
            ) from exc

    logger.info(
        "Vault Explorer upload completed",
        data={
            "event": "vault_explorer_upload_completed",
            "vault_name": vault_name,
            "path": normalized,
            "size_bytes": len(content),
            "event_sequence": mutation.event_sequence,
        },
    )
    return VaultPathMutationResponse(
        operation="upload",
        path=normalized,
        destination="",
        kind="file",
        message=f"Uploaded {normalized}.",
        metadata={
            "size_bytes": len(content),
            "task_id": mutation.task_id,
            "vault_id": mutation.vault_id,
            "event_sequence": mutation.event_sequence,
        },
    )


def resolve_vault_upload_target(
    *,
    vault_name: str,
    path: str,
) -> tuple[Path, str, Path]:
    """Resolve one create-only upload target inside a configured vault."""
    vault_root, normalized, full_path = _resolve_vault_file_path(vault_name, path)
    if full_path.exists():
        raise APIException(
            status_code=409,
            error_type="VaultPathExists",
            message=f"Vault path already exists: {normalized}",
            details={"path": normalized, "vault_name": vault_name},
        )
    return vault_root, normalized, full_path


def _mutate_vault_path_attributed(
    *,
    vault_name: str,
    vault_root: Path,
    normalized: str,
    full_path: Path,
    operation: str,
    destination: str,
    content: str,
) -> VaultPathMutationResponse:
    """Apply an explorer mutation under an established activity context."""
    if operation == "create_file":
        if full_path.exists():
            raise APIException(
                status_code=409,
                error_type="VaultPathExists",
                message=f"Vault path already exists: {normalized}",
                details={"path": normalized, "vault_name": vault_name},
            )
        try:
            mutation = write_vault_file(
                vault_path=vault_root,
                path=normalized,
                content=content,
                fail_if_exists=True,
                markdown_only=False,
            )
        except VaultMutationRejected as exc:
            raise _vault_path_mutation_error(
                exc, vault_name=vault_name, path=normalized
            ) from exc
        return VaultPathMutationResponse(
            operation=operation,
            path=normalized,
            destination="",
            kind="file",
            message=f"Created {normalized}.",
            metadata={
                "task_id": mutation.task_id,
                "vault_id": mutation.vault_id,
                "event_sequence": mutation.event_sequence,
            },
        )

    if operation == "create_directory":
        if full_path.exists():
            raise APIException(
                status_code=409,
                error_type="VaultPathExists",
                message=f"Vault path already exists: {normalized}",
                details={"path": normalized, "vault_name": vault_name},
            )
        result = make_vault_directory_operation(vault_path=vault_root, path=normalized)
        return _vault_path_operation_response(
            operation=operation,
            path=normalized,
            kind="directory",
            result=result,
        )

    if not full_path.exists():
        raise APIException(
            status_code=404,
            error_type="VaultPathNotFound",
            message=f"Vault path not found: {normalized}",
            details={"path": normalized, "vault_name": vault_name},
        )

    if operation == "move":
        normalized_destination = _normalize_vault_file_path(destination)
        kind: Literal["file", "directory"] = (
            "directory" if full_path.is_dir() else "file"
        )
        if kind == "directory":
            result = move_vault_directory_operation(
                vault_path=vault_root,
                path=normalized,
                destination=normalized_destination,
            )
        else:
            result = move_vault_path_operation(
                vault_path=vault_root,
                path=normalized,
                destination=normalized_destination,
                overwrite=False,
            )
        return _vault_path_operation_response(
            operation=operation,
            path=normalized,
            destination=normalized_destination,
            kind=kind,
            result=result,
        )

    if operation == "delete":
        kind = "directory" if full_path.is_dir() else "file"
        if kind == "directory" and any(full_path.iterdir()):
            raise APIException(
                status_code=409,
                error_type="VaultDirectoryNotEmpty",
                message=f"Cannot delete non-empty directory: {normalized}",
                details={"path": normalized, "vault_name": vault_name},
            )
        result = delete_vault_path_operation(
            vault_path=vault_root,
            path=normalized,
            confirm_path=normalized,
        )
        return _vault_path_operation_response(
            operation=operation,
            path=normalized,
            kind=kind,
            result=result,
        )

    raise APIException(
        status_code=400,
        error_type="InvalidVaultPathMutation",
        message=f"Unsupported vault path mutation: {operation}",
        details={"operation": operation, "path": normalized},
    )


@contextmanager
def _explorer_activity(
    *,
    label: str,
) -> Iterator[VaultActivityContext]:
    """Track one synchronous Explorer command as durable vault activity."""
    context = VaultActivityContext(
        activity_id=f"activity_{uuid.uuid4().hex}",
        kind="explorer",
        source="api",
        scope=None,
        label=label,
    )
    service = VaultStateService()
    with use_vault_activity(context):
        try:
            yield context
        except Exception:
            service.finish_activity(activity_id=context.activity_id, status="failed")
            raise
        else:
            service.finish_activity(activity_id=context.activity_id, status="completed")


def _vault_path_operation_response(
    *,
    operation: str,
    path: str,
    kind: Literal["file", "directory"],
    result: VaultFileOperationResult,
    destination: str = "",
) -> VaultPathMutationResponse:
    status = str(result.metadata.get("status") or "error")
    if status != "completed":
        status_code = (
            404 if status == "not_found" else 409 if status == "already_exists" else 400
        )
        raise APIException(
            status_code=status_code,
            error_type=str(
                result.metadata.get("error_type") or "VaultPathMutationFailed"
            ),
            message=str(result.return_value),
            details={"path": path, "destination": destination, **result.metadata},
        )
    return VaultPathMutationResponse(
        operation=operation,
        path=path,
        destination=destination,
        kind=kind,
        message=str(result.return_value),
        metadata=dict(result.metadata),
    )


def _vault_path_mutation_error(
    exc: VaultMutationRejected,
    *,
    vault_name: str,
    path: str,
) -> APIException:
    status_by_code = {
        "file_exists": 409,
        "file_not_found": 404,
        "invalid_path": 400,
    }
    return APIException(
        status_code=status_by_code.get(exc.code, 400),
        error_type=exc.code,
        message=str(exc),
        details={"path": path, "vault_name": vault_name},
    )


def _normalize_chat_reference_candidate(path: str) -> str:
    raw_path = str(path or "").strip().removeprefix("@").replace("\\", "/")
    if len(raw_path) > 1000:
        raise APIException(
            status_code=400,
            error_type="InvalidVaultReferencePath",
            message="Vault reference path is too long.",
            details={"path": raw_path[:100]},
        )
    if raw_path.startswith("/") or ".." in raw_path.split("/"):
        raise APIException(
            status_code=400,
            error_type="InvalidVaultReferencePath",
            message="Vault reference paths must stay relative to the vault.",
            details={"path": raw_path},
        )
    return normalize_vault_relative_path(raw_path)


def _resolved_chat_path_item(
    *,
    vault_root: Path,
    requested_path: str,
    candidate: Path,
    source: Literal["workspace", "vault"],
) -> VaultPathResolutionInfo | None:
    if not candidate.exists() or not (candidate.is_file() or candidate.is_dir()):
        return None
    try:
        relative = candidate.resolve().relative_to(vault_root).as_posix()
    except ValueError:
        return None
    if any(part.startswith(".") for part in Path(relative).parts):
        return None
    return VaultPathResolutionInfo(
        requested_path=requested_path,
        path=relative,
        kind="directory" if candidate.is_dir() else "file",
        source=source,
    )


def _list_vault_file_reference_children(
    *,
    vault_root: Path,
    base_dir: Path,
    workspace_path: str,
    limit: int,
    offset: int = 0,
) -> tuple[list[VaultFileReferenceInfo], bool]:
    items: list[VaultFileReferenceInfo] = []
    eligible_index = 0
    truncated = False
    for child in sorted(
        base_dir.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
    ):
        if not _is_file_reference_path(child):
            continue
        if eligible_index < offset:
            eligible_index += 1
            continue
        if len(items) >= limit:
            truncated = True
            break
        info = _vault_file_reference_info(
            vault_root=vault_root,
            path=child,
            workspace_path=workspace_path,
        )
        if info is not None:
            items.append(info)
        eligible_index += 1
    return items, truncated


def _search_vault_file_references(
    *,
    vault_root: Path,
    base_dir: Path,
    workspace_path: str,
    query: str,
    limit: int,
) -> tuple[list[VaultFileReferenceInfo], bool]:
    matches: list[VaultFileReferenceInfo] = []
    for child in base_dir.rglob("*"):
        if len(matches) > limit:
            break
        if not _is_file_reference_path(child):
            continue
        try:
            relative = child.resolve().relative_to(vault_root).as_posix()
        except ValueError:
            continue
        haystack = f"{child.name.lower()} {relative.lower()}"
        if query not in haystack:
            continue
        info = _vault_file_reference_info(
            vault_root=vault_root,
            path=child,
            workspace_path=workspace_path,
        )
        if info is not None:
            matches.append(info)
    ordered = sorted(
        matches,
        key=lambda item: (
            not item.in_workspace,
            item.kind != "directory",
            item.path.lower(),
        ),
    )
    return ordered[:limit], len(ordered) > limit


def _is_file_reference_path(path: Path) -> bool:
    """Return whether a filesystem path should be shown in the reference picker."""
    return not any(part.startswith(".") for part in path.parts)


def _vault_file_reference_info(
    *,
    vault_root: Path,
    path: Path,
    workspace_path: str,
) -> VaultFileReferenceInfo | None:
    try:
        relative = path.resolve().relative_to(vault_root).as_posix()
    except ValueError:
        return None
    is_directory = path.is_dir()
    try:
        stat_result = path.stat()
    except OSError:
        stat_result = None
    workspace_prefix = f"{workspace_path.rstrip('/')}/" if workspace_path else ""
    in_workspace = (
        not workspace_path
        or relative == workspace_path
        or relative.startswith(workspace_prefix)
    )
    has_children = False
    if is_directory:
        try:
            has_children = any(
                _is_file_reference_path(child) for child in path.iterdir()
            )
        except OSError:
            has_children = False
    return VaultFileReferenceInfo(
        name=path.name or relative,
        path=relative,
        kind="directory" if is_directory else "file",
        size_bytes=None if is_directory or stat_result is None else stat_result.st_size,
        modified_at=(
            None
            if stat_result is None
            else datetime.fromtimestamp(stat_result.st_mtime, tz=UTC)
        ),
        has_children=has_children,
        in_workspace=in_workspace,
    )
