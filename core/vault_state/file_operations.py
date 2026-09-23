"""Unified vault file operation helpers.

This layer owns operation-level validation and text mutation behavior. Final
write/delete/move recording still belongs to ``core.vault_state.file_mutations``.
"""

from __future__ import annotations

import glob
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai.messages import BinaryContent

from core.chunking import (
    build_input_files_prompt,
    default_chunking_policy,
    evaluate_markdown_image_policy,
    parse_markdown_chunks,
)
from core.constants import SUPPORTED_READ_FILE_TYPES, VIRTUAL_MOUNTS
from core.settings import (
    get_auto_cache_max_tokens,
    get_chunking_max_image_bytes_per_image,
    get_chunking_max_image_mb_per_image,
    get_file_list_max_results,
    get_file_search_timeout_seconds,
)
from core.utils.hash import hash_file_bytes, hash_file_content
from core.utils.image_inputs import build_image_tool_payload
from core.vault_state.file_mutations import (
    DirectoryCleanupResult,
    DirectoryMoveResult,
    RecordedMutationResult,
    VaultMutationRejected,
    append_vault_file,
    delete_empty_vault_directory_tree,
    delete_vault_file,
    move_vault_directory,
    move_vault_file,
    record_vault_directory_mutation,
    replace_vault_file_content,
    vault_directory_mutation_lock,
    vault_file_mutation_lock,
    write_vault_file,
)
from core.vault_state.pathing import (
    normalize_vault_relative_path,
    resolve_vault_relative_path,
)
from core.vault_state.search import VaultContentSearchError, search_vault_content


class VaultFileOperationRejected(Exception):
    """Raised when a vault file operation fails before or during mutation."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class VaultFileOperationResult:
    """Result envelope for user-facing vault file operations."""

    return_value: Any
    metadata: dict[str, Any]
    content: Any | None = None


@dataclass(frozen=True)
class VaultTextTarget:
    """Resolved vault text-file target."""

    requested_path: str
    path: str
    full_path: Path

    @property
    def requested_path_metadata(self) -> dict[str, str]:
        if self.requested_path and self.requested_path != self.path:
            return {"requested_path": self.requested_path}
        return {}


@dataclass(frozen=True)
class TextReplacement:
    """One exact text replacement request."""

    original_text: str
    replacement_text: str
    edit_id: str | None = None


@dataclass(frozen=True)
class TextMutationResult:
    """Result of a text mutation operation."""

    path: str
    requested_path: str
    replacement_count: int
    mutation: RecordedMutationResult

    @property
    def requested_path_metadata(self) -> dict[str, str]:
        if self.requested_path and self.requested_path != self.path:
            return {"requested_path": self.requested_path}
        return {}


@dataclass(frozen=True)
class PreparedTextMutation:
    """Validated text mutation content ready to write."""

    path: str
    requested_path: str
    content: str
    replacement_count: int

    @property
    def requested_path_metadata(self) -> dict[str, str]:
        if self.requested_path and self.requested_path != self.path:
            return {"requested_path": self.requested_path}
        return {}


@dataclass(frozen=True)
class PreparedCreateFile:
    """Validated create-file operation ready to write."""

    path: str
    content: str


@dataclass(frozen=True)
class PreparedDeleteFile:
    """Validated delete-file operation ready to write."""

    path: str


@dataclass(frozen=True)
class PreparedMoveFile:
    """Validated move-file operation ready to write."""

    path: str
    destination: str


def read_vault_file_operation(
    *,
    vault_path: str | Path,
    path: str,
    start_line: int = 0,
    line_count: int = 0,
) -> VaultFileOperationResult:
    """Read a vault text or image file, optionally returning a text line slice."""
    if _virtual_mount_key(path):
        return _read_virtual_file_operation(
            path=path, start_line=start_line, line_count=line_count
        )
    target = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=False,
        prefer_markdown_extension=True,
    )
    if target.full_path.is_dir():
        if _should_try_markdown_file(target.requested_path):
            return list_vault_paths_operation(
                vault_path=vault_path,
                path=target.requested_path,
                recursive=False,
            )
        return _operation_result(
            f"Cannot read '{target.path}' - this is a directory, not a file.",
            operation="read",
            path=target.path,
            status="invalid_target",
            exists=True,
            error_type="is_directory",
            metadata=target.requested_path_metadata,
        )
    return _read_existing_vault_file_operation(
        target=target,
        vault_path=Path(vault_path),
        start_line=start_line,
        line_count=line_count,
    )


def frontmatter_vault_files_operation(
    *,
    vault_path: str | Path,
    path: str = "",
    keys: str = "",
) -> VaultFileOperationResult:
    """Extract YAML frontmatter from matching vault markdown files."""
    raw_path = path.strip()
    pattern_path = raw_path or "*"
    _reject_virtual_mount_path(pattern_path)
    if ".." in pattern_path or pattern_path.startswith("/"):
        return _operation_result(
            "Path cannot contain '..' or start with '/'.",
            operation="frontmatter",
            path=raw_path,
            status="invalid_target",
            error_type="invalid_path",
        )

    vault_root = Path(vault_path).resolve()
    candidate = (vault_root / pattern_path).resolve()
    _ensure_within_root(vault_root, candidate)
    if candidate.is_dir():
        pattern_path = str(Path(pattern_path) / "*")

    filter_keys = [key.strip() for key in keys.split(",") if key.strip()]
    items: list[dict[str, Any]] = []
    for match in sorted(glob.glob(str(vault_root / pattern_path), recursive=False)):
        full_path = Path(match).resolve()
        if (
            full_path.suffix.lower() not in {".md", ".markdown"}
            or not full_path.is_file()
        ):
            continue
        _ensure_within_root(vault_root, full_path)
        relative_path = _relative_to_root(vault_root, full_path)
        if any(part.startswith(".") for part in Path(relative_path).parts):
            continue
        try:
            frontmatter = _parse_markdown_frontmatter(
                full_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, yaml.YAMLError):
            frontmatter = {}
        if filter_keys:
            frontmatter = {
                key: frontmatter[key] for key in filter_keys if key in frontmatter
            }
        items.append({"path": relative_path, "frontmatter": frontmatter})

    if not items:
        message = f"No markdown files found for path '{pattern_path}'"
    else:
        lines = []
        for item in items:
            values = ", ".join(
                f"{key}: {value}" for key, value in item["frontmatter"].items()
            )
            lines.append(f"  {item['path']}: {{{values}}}")
        message = f"Frontmatter ({len(items)} files):\n" + "\n".join(lines)
    return _operation_result(
        message,
        operation="frontmatter",
        path=pattern_path,
        status="completed",
        metadata={"file_count": len(items), "items": items},
    )


def list_vault_paths_operation(
    *,
    vault_path: str | Path,
    path: str = "",
    recursive: bool = False,
    max_results: int | None = None,
) -> VaultFileOperationResult:
    """List vault files and directories matching a path or glob."""
    if _virtual_mount_key(path):
        return _list_virtual_paths_operation(
            path=path,
            recursive=recursive,
            max_results=(
                _default_list_max_results() if max_results is None else max_results
            ),
        )
    vault_root = Path(vault_path).resolve()
    max_count = _default_list_max_results() if max_results is None else max_results
    raw_path = path.strip()
    scope_relative_path: str | None = None
    pattern_path = raw_path
    if not pattern_path or pattern_path == ".":
        pattern_path = "**/*" if recursive else "*"
    _reject_virtual_mount_path(pattern_path)
    if ".." in pattern_path or pattern_path.startswith("/"):
        raise VaultFileOperationRejected(
            "invalid_path",
            "Path cannot contain '..' or start with '/'.",
            details={"path": raw_path},
        )

    is_glob = any(ch in pattern_path for ch in "*?[")
    if not is_glob:
        candidate = (vault_root / pattern_path).resolve()
        _ensure_within_root(vault_root, candidate)
        if candidate.is_dir():
            scope_relative_path = _relative_to_root(vault_root, candidate)
            pattern_path = str(Path(pattern_path) / ("**/*" if recursive else "*"))

    files, directories, file_count, directory_count = _collect_limited_matches(
        pattern=vault_root / pattern_path,
        recursive=recursive or "**" in pattern_path,
        root_path=vault_root,
        max_results=max_count,
    )
    truncated = max_count > 0 and file_count + directory_count > max_count
    empty_candidates = _empty_directory_candidates(
        vault_path=vault_root,
        directories=directories,
        scope_relative_path=scope_relative_path,
        include_scope_when_empty=file_count == 0 and directory_count == 0,
    )

    if file_count == 0 and directory_count == 0:
        message = f"No files or directories found for path '{pattern_path}'"
    else:
        parts = []
        if directories:
            parts.append(
                f"Directories ({len(directories)}):\n"
                + "\n".join(f"  {directory}/" for directory in directories)
            )
        if files:
            parts.append(
                f"Files ({len(files)}):\n"
                + "\n".join(f"  {file_path}" for file_path in files)
            )
        if truncated:
            parts.append(
                f"... truncated to {max_count} results. Narrow your path or disable recursion."
            )
        message = "\n\n".join(parts)

    return _operation_result(
        message,
        operation="list",
        path=pattern_path,
        status="completed",
        exists=True,
        metadata={
            "directory_count": len(directories),
            "file_count": len(files),
            "directories": directories,
            "files": files,
            "empty_directory_candidates": empty_candidates,
            "empty_directory_candidate_count": len(empty_candidates),
            "truncated": truncated,
        },
    )


def search_vault_files_operation(
    *,
    vault_path: str | Path,
    path: str,
    search_term: str,
) -> VaultFileOperationResult:
    """Search text files within the vault using ripgrep."""
    query = search_term.strip()
    if not query:
        return _operation_result(
            "Search requires a search pattern in 'search_term'.",
            operation="search",
            path=path,
            search_term=query,
            status="error",
            error_type="missing_query",
        )

    vault_root = Path(vault_path).resolve()
    result_root = vault_root
    search_path = path.strip()
    if search_path:
        mount_key = _virtual_mount_key(search_path)
        if mount_key:
            result_root = _virtual_mount_root(mount_key)
            relative_scope = (
                search_path.strip().lstrip("./")[len(mount_key) :].lstrip("/")
            )
            if ".." in relative_scope.split("/"):
                return _operation_result(
                    "Path cannot contain '..' for virtual mounts.",
                    operation="search",
                    path=search_path,
                    search_term=query,
                    status="invalid_target",
                    error_type="invalid_path",
                )
            search_roots = _resolve_search_roots(result_root, relative_scope)
            result_prefix = mount_key
        elif ".." in search_path or search_path.startswith("/"):
            return _operation_result(
                "Path cannot contain '..' or start with '/'.",
                operation="search",
                path=search_path,
                search_term=query,
                status="invalid_target",
                error_type="invalid_path",
            )
        else:
            search_roots = _resolve_search_roots(vault_root, search_path)
            result_prefix = ""
    else:
        search_roots = [vault_root]
        result_prefix = ""

    if not search_roots:
        return _operation_result(
            f"No matches found for '{query}' in text files",
            operation="search",
            path=search_path,
            search_term=query,
            status="completed",
            exists=True,
            metadata={"match_count": 0, "matches": []},
        )

    if not result_prefix and len(search_roots) == 1 and search_roots[0].is_dir():
        try:
            structured = search_vault_content(
                vault_path=vault_root,
                path=(
                    search_roots[0].relative_to(vault_root).as_posix()
                    if search_roots[0] != vault_root
                    else ""
                ),
                query=query,
                limit=_default_list_max_results(),
                timeout_seconds=_default_search_timeout_seconds(),
            )
        except VaultContentSearchError as exc:
            return _operation_result(
                str(exc),
                operation="search",
                path=search_path,
                search_term=query,
                status="error",
                error_type=exc.code,
            )
        matches = [
            f"{match.path}:{match.line}:{match.snippet}" for match in structured.matches
        ]
        if not matches:
            return _operation_result(
                f"No matches found for '{query}' in text files",
                operation="search",
                path=search_path,
                search_term=query,
                status="completed",
                metadata={"match_count": 0, "matches": [], "truncated": False},
            )
        return _operation_result(
            "\n".join(matches),
            operation="search",
            path=search_path,
            search_term=query,
            status="completed",
            metadata={
                "match_count": len(matches),
                "matches": matches,
                "truncated": structured.truncated,
            },
        )

    command = [
        "rg",
        "--no-heading",
        "--with-filename",
        "--line-number",
        "--color",
        "never",
        "--ignore-case",
        query,
        *(str(root) for root in search_roots),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_default_search_timeout_seconds(),
            check=False,
        )
    except FileNotFoundError:
        return _operation_result(
            "Error: ripgrep (rg) not found. Please install ripgrep to use search functionality.",
            operation="search",
            path=search_path,
            search_term=query,
            status="error",
            error_type="ripgrep_not_found",
        )
    except subprocess.TimeoutExpired:
        return _operation_result(
            f"Search timed out for '{query}'.",
            operation="search",
            path=search_path,
            search_term=query,
            status="error",
            error_type="timeout",
        )
    if completed.returncode not in {0, 1}:
        return _operation_result(
            completed.stderr.strip() or f"Search failed for '{query}'.",
            operation="search",
            path=search_path,
            search_term=query,
            status="error",
            error_type="search_failed",
        )

    matches = _format_rg_matches(
        completed.stdout, result_root, result_prefix=result_prefix
    )
    if not matches:
        return _operation_result(
            f"No matches found for '{query}' in text files",
            operation="search",
            path=search_path,
            search_term=query,
            status="completed",
            metadata={"match_count": 0, "matches": []},
        )
    return _operation_result(
        "\n".join(matches),
        operation="search",
        path=search_path,
        search_term=query,
        status="completed",
        metadata={"match_count": len(matches), "matches": matches},
    )


def write_vault_file_operation(
    *,
    vault_path: str | Path,
    path: str,
    content: str,
    overwrite: bool = False,
) -> VaultFileOperationResult:
    """Create a markdown file, or replace full content when overwrite is true."""
    if overwrite:
        return overwrite_vault_file_operation(
            vault_path=vault_path, path=path, content=content
        )
    try:
        mutation = write_vault_file(
            vault_path=vault_path,
            path=path,
            content=content,
            fail_if_exists=True,
            markdown_only=True,
        )
    except VaultMutationRejected as exc:
        if exc.code != "file_exists":
            raise
        return _operation_result(
            f"Cannot write to '{path}' - file already exists.",
            operation="write",
            path=path,
            status="already_exists",
            exists=True,
            error_type="file_exists",
        )
    return _mutation_result(
        f"Successfully created new file '{path}' with {len(content)} characters",
        operation="write",
        path=path,
        mutation=mutation,
        exists=True,
        metadata={"content_chars": len(content), "overwrote": False},
    )


def overwrite_vault_file_operation(
    *,
    vault_path: str | Path,
    path: str,
    content: str,
) -> VaultFileOperationResult:
    """Create or overwrite a markdown file with full content."""
    if not path.strip():
        return _operation_result(
            "Path is required for write.",
            operation="write",
            path=path,
            status="error",
            error_type="invalid_path",
        )
    target = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=True,
        prefer_markdown_extension=True,
    )
    existed_before = target.full_path.exists()
    if existed_before:
        mutation = replace_vault_file_content(
            vault_path=vault_path,
            path=target.path,
            content=content,
            operation="write",
            markdown_only=True,
        )
    else:
        mutation = write_vault_file(
            vault_path=vault_path,
            path=target.path,
            content=content,
            fail_if_exists=False,
            markdown_only=True,
        )
    return _mutation_result(
        (
            f"Successfully overwrote '{target.path}' with {len(content)} characters"
            if existed_before
            else f"Successfully created new file '{target.path}' with {len(content)} characters"
        ),
        operation="write",
        path=target.path,
        mutation=mutation,
        exists=True,
        metadata={
            "content_chars": len(content),
            "overwrote": existed_before,
            **target.requested_path_metadata,
        },
    )


def append_vault_file_operation(
    *,
    vault_path: str | Path,
    path: str,
    content: str,
) -> VaultFileOperationResult:
    """Append text to an existing markdown file."""
    try:
        mutation = append_vault_file(
            vault_path=vault_path,
            path=path,
            content=content,
            markdown_only=True,
        )
    except VaultMutationRejected as exc:
        if exc.code != "file_not_found":
            raise
        return _operation_result(
            f"Cannot append to '{path}' - file does not exist.",
            operation="append",
            path=path,
            status="not_found",
            exists=False,
            error_type="file_not_found",
        )
    return _mutation_result(
        f"Successfully appended {len(content)} characters to '{path}'",
        operation="append",
        path=path,
        mutation=mutation,
        exists=True,
        metadata={"content_chars": len(content)},
    )


def replace_text_vault_file_operation(
    *,
    vault_path: str | Path,
    path: str,
    old_text: str,
    new_text: str,
    count: int,
) -> VaultFileOperationResult:
    """Replace exact text in an existing markdown file."""
    try:
        result = replace_text(
            vault_path=vault_path,
            path=path,
            old_text=old_text,
            new_text=new_text,
            count=count,
            operation="replace_text",
            markdown_only=True,
            prefer_markdown_extension=True,
        )
    except VaultFileOperationRejected as exc:
        return _rejected_result(exc, operation="replace_text", fallback_path=path)
    return _mutation_result(
        f"Successfully replaced {result.replacement_count} occurrence(s) in '{result.path}'",
        operation="replace_text",
        path=result.path,
        mutation=result.mutation,
        exists=True,
        metadata={
            "replacement_count": result.replacement_count,
            **result.requested_path_metadata,
        },
    )


def edit_vault_line_operation(
    *,
    vault_path: str | Path,
    path: str,
    line_number: int,
    old_text: str,
    new_text: str,
) -> VaultFileOperationResult:
    """Replace one validated line in an existing markdown file."""
    target = resolve_markdown_text_target(vault_path=vault_path, path=path)
    with vault_file_mutation_lock(vault_path, target.full_path):
        return _edit_vault_line_locked(
            vault_path=vault_path,
            target=target,
            line_number=line_number,
            old_text=old_text,
            new_text=new_text,
        )


def _edit_vault_line_locked(
    *,
    vault_path: str | Path,
    target: VaultTextTarget,
    line_number: int,
    old_text: str,
    new_text: str,
) -> VaultFileOperationResult:
    """Replace one validated line while holding the target mutation lock."""
    if not target.full_path.exists():
        return _operation_result(
            f"Cannot edit '{target.path}' - file does not exist",
            operation="edit_line",
            path=target.path,
            status="not_found",
            exists=False,
            error_type="file_not_found",
            metadata=target.requested_path_metadata,
        )
    if line_number < 1:
        return _operation_result(
            f"Invalid line_number {line_number} - must be >= 1",
            operation="edit_line",
            path=target.path,
            status="error",
            exists=True,
            error_type="invalid_line_number",
            metadata=target.requested_path_metadata,
        )
    if target.full_path.is_dir():
        return _operation_result(
            f"Cannot edit '{target.path}' - this is a directory, not a file",
            operation="edit_line",
            path=target.path,
            status="invalid_target",
            exists=True,
            error_type="is_directory",
            metadata=target.requested_path_metadata,
        )

    with target.full_path.open("r", encoding="utf-8", newline="") as file:
        lines = file.readlines()
    if line_number > len(lines):
        return _operation_result(
            f"Line {line_number} does not exist - file only has {len(lines)} lines",
            operation="edit_line",
            path=target.path,
            status="invalid_target",
            exists=True,
            error_type="line_not_found",
            metadata={
                "line_count": len(lines),
                **target.requested_path_metadata,
            },
        )

    original_line = lines[line_number - 1]
    current_line = original_line.rstrip("\r\n")
    if current_line != old_text:
        return _operation_result(
            (
                f"Line {line_number} content mismatch. "
                f"Expected: '{old_text}', Found: '{current_line}'"
            ),
            operation="edit_line",
            path=target.path,
            status="error",
            exists=True,
            error_type="content_mismatch",
            metadata={
                "line_number": line_number,
                **target.requested_path_metadata,
            },
        )

    line_ending = "\r\n" if original_line.endswith("\r\n") else "\n"
    if "\n" in new_text:
        lines[line_number - 1 : line_number] = [
            line + line_ending for line in new_text.split("\n")
        ]
    else:
        lines[line_number - 1] = new_text + line_ending
    mutation = replace_vault_file_content(
        vault_path=vault_path,
        path=target.path,
        content="".join(lines),
        operation="edit_line",
        markdown_only=True,
    )
    return _mutation_result(
        f"Successfully edited line {line_number} in '{target.path}'",
        operation="edit_line",
        path=target.path,
        mutation=mutation,
        exists=True,
        metadata={
            "line_number": line_number,
            **target.requested_path_metadata,
        },
    )


def move_vault_path_operation(
    *,
    vault_path: str | Path,
    path: str,
    destination: str,
    overwrite: bool = False,
) -> VaultFileOperationResult:
    """Move a vault file, optionally overwriting an existing destination."""
    destination_target = resolve_text_target(
        vault_path=vault_path,
        path=destination,
        markdown_only=False,
    )
    overwrote_destination = destination_target.full_path.exists()
    try:
        source_mutation, destination_mutation = move_vault_file(
            vault_path=vault_path,
            path=path,
            destination=destination,
            overwrite=overwrite,
        )
    except VaultMutationRejected as exc:
        status = "not_found" if exc.code == "source_not_found" else "already_exists"
        if exc.code not in {"source_not_found", "destination_exists"}:
            raise
        return _operation_result(
            str(exc),
            operation="move",
            path=path,
            destination=destination,
            status=status,
            exists=exc.code != "source_not_found",
            error_type=exc.code,
        )
    return _operation_result(
        f"Successfully moved '{path}' to '{destination}'",
        operation="move",
        path=path,
        destination=destination,
        status="completed",
        exists=True,
        metadata={
            "overwrote_destination": overwrite and overwrote_destination,
            "task_id": source_mutation.task_id or destination_mutation.task_id,
            "vault_id": source_mutation.vault_id,
        },
    )


def move_vault_directory_operation(
    *,
    vault_path: str | Path,
    path: str,
    destination: str,
) -> VaultFileOperationResult:
    """Move one directory tree without creating per-descendant file mutations."""
    try:
        move_result: DirectoryMoveResult = move_vault_directory(
            vault_path=vault_path,
            path=path,
            destination=destination,
        )
    except VaultMutationRejected as exc:
        status_by_code = {
            "source_not_found": "not_found",
            "destination_exists": "already_exists",
        }
        return _operation_result(
            str(exc),
            operation="move",
            path=path,
            destination=destination,
            status=status_by_code.get(exc.code, "error"),
            exists=exc.code != "source_not_found",
            error_type=exc.code,
        )
    return _operation_result(
        f"Successfully moved directory '{path}' to '{destination}'",
        operation="move",
        path=path,
        destination=destination,
        status="completed",
        exists=True,
        metadata={
            "vault_id": move_result.vault_id,
            "event_sequence": move_result.event_sequence,
            "descendant_file_count": move_result.descendant_file_count,
            "descendant_directory_count": move_result.descendant_directory_count,
        },
    )


def delete_vault_path_operation(
    *,
    vault_path: str | Path,
    path: str,
    confirm_path: str,
) -> VaultFileOperationResult:
    """Delete a vault file or empty directory tree after path confirmation."""
    if path != confirm_path:
        return _operation_result(
            f"Path confirmation failed - path '{path}' does not match confirm_path '{confirm_path}'",
            operation="delete",
            path=path,
            status="error",
            error_type="confirmation_failed",
        )
    target = resolve_text_target(vault_path=vault_path, path=path, markdown_only=False)
    if not target.full_path.exists():
        return _operation_result(
            f"Cannot delete '{target.path}' - file does not exist",
            operation="delete",
            path=target.path,
            status="not_found",
            exists=False,
            error_type="file_not_found",
            metadata=target.requested_path_metadata,
        )
    if target.full_path.is_dir():
        return _delete_empty_directory_operation(
            vault_path=vault_path, path=target.path
        )
    try:
        mutation = delete_vault_file(vault_path=vault_path, path=target.path)
    except VaultMutationRejected as exc:
        if exc.code != "file_not_found":
            raise
        return _operation_result(
            f"Cannot delete '{target.path}' - file does not exist",
            operation="delete",
            path=target.path,
            status="not_found",
            exists=False,
            error_type="file_not_found",
        )
    return _mutation_result(
        f"Successfully deleted '{target.path}'",
        operation="delete",
        path=target.path,
        mutation=mutation,
        exists=False,
        metadata={"target_type": "file"},
    )


def make_vault_directory_operation(
    *,
    vault_path: str | Path,
    path: str,
) -> VaultFileOperationResult:
    """Create a directory within the vault."""
    target = resolve_text_target(vault_path=vault_path, path=path, markdown_only=False)
    with vault_directory_mutation_lock(vault_path, target.full_path):
        before_exists = target.full_path.exists()
        if before_exists and not target.full_path.is_dir():
            raise VaultFileOperationRejected(
                "file_exists",
                f"Cannot create directory '{target.path}' because a file exists there.",
            )
        target.full_path.mkdir(parents=True, exist_ok=True)
        if not before_exists:
            record_vault_directory_mutation(
                vault_path=vault_path,
                path=target.path,
                operation="mkdir",
                before_exists=False,
                after_exists=True,
            )
    return _operation_result(
        f"Successfully created directory '{target.path}'",
        operation="mkdir",
        path=target.path,
        status="completed",
        exists=True,
        metadata=target.requested_path_metadata,
    )


def resolve_text_target(
    *,
    vault_path: str | Path,
    path: str,
    markdown_only: bool,
    prefer_markdown_extension: bool = False,
) -> VaultTextTarget:
    """Resolve a vault-relative text target with optional markdown-first lookup."""
    requested_path = normalize_vault_relative_path(path)
    _reject_virtual_mount_path(requested_path)
    vault_root = Path(vault_path).resolve()

    if prefer_markdown_extension and _should_try_markdown_file(requested_path):
        markdown_path = f"{requested_path}.md"
        markdown_full_path = resolve_vault_relative_path(
            vault_path=vault_root,
            path=markdown_path,
            markdown_only=markdown_only,
        )
        if markdown_full_path.is_file():
            return VaultTextTarget(
                requested_path=requested_path,
                path=markdown_path,
                full_path=markdown_full_path,
            )

    full_path = resolve_vault_relative_path(
        vault_path=vault_root,
        path=requested_path,
        markdown_only=markdown_only,
    )
    return VaultTextTarget(
        requested_path=requested_path,
        path=requested_path,
        full_path=full_path,
    )


def resolve_markdown_text_target(
    *, vault_path: str | Path, path: str
) -> VaultTextTarget:
    """Resolve a markdown text mutation target with extensionless .md preference."""
    return resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=True,
        prefer_markdown_extension=True,
    )


def replace_text(
    *,
    vault_path: str | Path,
    path: str,
    old_text: str,
    new_text: str,
    count: int,
    operation: str,
    markdown_only: bool = True,
    prefer_markdown_extension: bool = False,
    expected_sha256: str | None = None,
    require_match_count: int | None = None,
) -> TextMutationResult:
    """Replace text in one vault file after operation-level validation."""
    target = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=markdown_only,
        prefer_markdown_extension=prefer_markdown_extension,
    )
    with vault_file_mutation_lock(vault_path, target.full_path):
        return _replace_text_locked(
            vault_path=vault_path,
            target=target,
            old_text=old_text,
            new_text=new_text,
            count=count,
            operation=operation,
            markdown_only=markdown_only,
            expected_sha256=expected_sha256,
            require_match_count=require_match_count,
        )


def _replace_text_locked(
    *,
    vault_path: str | Path,
    target: VaultTextTarget,
    old_text: str,
    new_text: str,
    count: int,
    operation: str,
    markdown_only: bool,
    expected_sha256: str | None,
    require_match_count: int | None,
) -> TextMutationResult:
    """Replace text while holding the target mutation lock."""
    current = _read_existing_text_file(target)
    if count < 1:
        raise VaultFileOperationRejected(
            "invalid_count",
            f"Invalid count {count} - must be >= 1",
            details={
                "count": count,
                "path": target.path,
                **target.requested_path_metadata,
            },
        )
    _check_expected_sha256(
        target=target, current=current, expected_sha256=expected_sha256
    )

    match_count = current.count(old_text)
    if match_count == 0:
        raise VaultFileOperationRejected(
            "text_not_found",
            f"Text not found in '{target.path}': '{old_text}'",
            details={"path": target.path, **target.requested_path_metadata},
        )
    if require_match_count is not None and match_count != require_match_count:
        raise VaultFileOperationRejected(
            "text_match_count_mismatch",
            f"Text must match exactly {require_match_count} time(s) in {target.path}.",
            details={
                "path": target.path,
                "match_count": match_count,
                "required_match_count": require_match_count,
                **target.requested_path_metadata,
            },
        )

    replacement_count = min(match_count, count)
    mutation = replace_vault_file_content(
        vault_path=vault_path,
        path=target.path,
        content=current.replace(old_text, new_text, count),
        operation=operation,
        markdown_only=markdown_only,
    )
    return TextMutationResult(
        path=target.path,
        requested_path=target.requested_path,
        replacement_count=replacement_count,
        mutation=mutation,
    )


def replace_text_once_with_hash(
    *,
    vault_path: str | Path,
    path: str,
    original_text: str,
    replacement_text: str,
    expected_sha256: str,
    operation: str,
    markdown_only: bool = False,
) -> TextMutationResult:
    """Replace exactly one text occurrence after checking the file content hash."""
    return replace_text(
        vault_path=vault_path,
        path=path,
        old_text=original_text,
        new_text=replacement_text,
        count=1,
        operation=operation,
        markdown_only=markdown_only,
        expected_sha256=expected_sha256,
        require_match_count=1,
    )


def apply_text_replacements_once(
    *,
    vault_path: str | Path,
    path: str,
    replacements: list[TextReplacement],
    expected_sha256: str | None,
    operation: str,
    markdown_only: bool = False,
) -> TextMutationResult:
    """Apply validated exact-once replacements to one file and write it once."""
    target = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=markdown_only,
    )
    with vault_file_mutation_lock(vault_path, target.full_path):
        prepared = prepare_text_replacements_once(
            vault_path=vault_path,
            path=path,
            replacements=replacements,
            expected_sha256=expected_sha256,
            markdown_only=markdown_only,
        )
        return write_prepared_text_mutation(
            vault_path=vault_path,
            prepared=prepared,
            operation=operation,
            markdown_only=markdown_only,
        )


def prepare_text_replacements_once(
    *,
    vault_path: str | Path,
    path: str,
    replacements: list[TextReplacement],
    expected_sha256: str | None,
    markdown_only: bool = False,
) -> PreparedTextMutation:
    """Validate exact-once replacements and return the resulting full content."""
    target = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=markdown_only,
    )
    current = _read_existing_text_file(target)
    _check_expected_sha256(
        target=target, current=current, expected_sha256=expected_sha256
    )

    updated = current
    for replacement in replacements:
        if not replacement.original_text:
            raise VaultFileOperationRejected(
                "invalid_edit",
                _edit_message(replacement, "has empty original text."),
                details={"path": target.path, **_edit_details(replacement)},
            )
        match_count = updated.count(replacement.original_text)
        if match_count != 1:
            raise VaultFileOperationRejected(
                "text_match_count_mismatch",
                f"Original text no longer matches exactly once in {target.path}.",
                details={
                    "path": target.path,
                    "match_count": match_count,
                    **_edit_details(replacement),
                },
            )
        updated = updated.replace(
            replacement.original_text,
            replacement.replacement_text,
            1,
        )

    return PreparedTextMutation(
        path=target.path,
        requested_path=target.requested_path,
        content=updated,
        replacement_count=len(replacements),
    )


def write_prepared_text_mutation(
    *,
    vault_path: str | Path,
    prepared: PreparedTextMutation,
    operation: str,
    markdown_only: bool = False,
) -> TextMutationResult:
    """Write a prepared text mutation through the recorded mutation path."""
    mutation = replace_vault_file_content(
        vault_path=vault_path,
        path=prepared.path,
        content=prepared.content,
        operation=operation,
        markdown_only=markdown_only,
    )
    return TextMutationResult(
        path=prepared.path,
        requested_path=prepared.requested_path,
        replacement_count=prepared.replacement_count,
        mutation=mutation,
    )


def replace_full_text_content(
    *,
    vault_path: str | Path,
    path: str,
    content: str,
    operation: str,
    expected_sha256: str | None = None,
    markdown_only: bool = False,
) -> TextMutationResult:
    """Replace a full text file after an optional current-content hash check."""
    target = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=markdown_only,
    )
    current = _read_existing_text_file(target)
    _check_expected_sha256(
        target=target, current=current, expected_sha256=expected_sha256
    )
    mutation = replace_vault_file_content(
        vault_path=vault_path,
        path=target.path,
        content=content,
        operation=operation,
        markdown_only=markdown_only,
    )
    return TextMutationResult(
        path=target.path,
        requested_path=target.requested_path,
        replacement_count=1,
        mutation=mutation,
    )


def prepare_create_file(
    *,
    vault_path: str | Path,
    path: str,
    content: str,
    markdown_only: bool = False,
) -> PreparedCreateFile:
    """Validate a create-file operation without writing."""
    target = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=markdown_only,
    )
    if target.full_path.exists():
        raise VaultFileOperationRejected(
            "file_exists",
            f"Vault file already exists: {target.path}",
            details={"path": target.path, **target.requested_path_metadata},
        )
    return PreparedCreateFile(path=target.path, content=content)


def write_prepared_create_file(
    *,
    vault_path: str | Path,
    prepared: PreparedCreateFile,
    markdown_only: bool = False,
) -> RecordedMutationResult:
    """Write a prepared create-file operation through the recorded mutation path."""
    return write_vault_file(
        vault_path=vault_path,
        path=prepared.path,
        content=prepared.content,
        fail_if_exists=True,
        markdown_only=markdown_only,
    )


def prepare_delete_file(
    *,
    vault_path: str | Path,
    path: str,
    expected_sha256: str | None,
    markdown_only: bool = False,
) -> PreparedDeleteFile:
    """Validate a delete-file operation without writing."""
    target = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=markdown_only,
    )
    _check_existing_file_hash(target=target, expected_sha256=expected_sha256)
    return PreparedDeleteFile(path=target.path)


def write_prepared_delete_file(
    *,
    vault_path: str | Path,
    prepared: PreparedDeleteFile,
    markdown_only: bool = False,
) -> RecordedMutationResult:
    """Write a prepared delete-file operation through the recorded mutation path."""
    return delete_vault_file(
        vault_path=vault_path,
        path=prepared.path,
        markdown_only=markdown_only,
    )


def prepare_move_file(
    *,
    vault_path: str | Path,
    path: str,
    destination: str,
    expected_sha256: str | None,
    markdown_only: bool = False,
) -> PreparedMoveFile:
    """Validate a move-file operation without writing."""
    normalized_destination = normalize_vault_relative_path(destination)
    if not normalized_destination:
        raise VaultFileOperationRejected(
            "invalid_destination",
            "Move destination is required.",
            details={"path": normalize_vault_relative_path(path)},
        )
    source = resolve_text_target(
        vault_path=vault_path,
        path=path,
        markdown_only=markdown_only,
    )
    destination_target = resolve_text_target(
        vault_path=vault_path,
        path=normalized_destination,
        markdown_only=markdown_only,
    )
    _check_existing_file_hash(target=source, expected_sha256=expected_sha256)
    if destination_target.full_path.exists():
        raise VaultFileOperationRejected(
            "destination_exists",
            f"Vault destination already exists: {destination_target.path}",
            details={
                "path": source.path,
                "destination": destination_target.path,
                **source.requested_path_metadata,
            },
        )
    return PreparedMoveFile(path=source.path, destination=destination_target.path)


def write_prepared_move_file(
    *,
    vault_path: str | Path,
    prepared: PreparedMoveFile,
    markdown_only: bool = False,
) -> tuple[RecordedMutationResult, RecordedMutationResult]:
    """Write a prepared move-file operation through the recorded mutation path."""
    return move_vault_file(
        vault_path=vault_path,
        path=prepared.path,
        destination=prepared.destination,
        overwrite=False,
        markdown_only=markdown_only,
    )


def _read_existing_vault_file_operation(
    *,
    target: VaultTextTarget,
    vault_path: Path,
    start_line: int,
    line_count: int,
) -> VaultFileOperationResult:
    if not target.full_path.exists():
        return _operation_result(
            f"Cannot read '{target.path}' - file does not exist.",
            operation="read",
            path=target.path,
            status="not_found",
            exists=False,
            error_type="file_not_found",
            metadata=target.requested_path_metadata,
        )

    extension = target.full_path.suffix.lower()
    if (
        start_line <= 0
        and line_count <= 0
        and SUPPORTED_READ_FILE_TYPES.get(extension) == "image"
    ):
        binary_content = BinaryContent.from_path(target.full_path)
        image_size_bytes = len(binary_content.data)
        if get_chunking_max_image_bytes_per_image() > 0 and (
            image_size_bytes > get_chunking_max_image_bytes_per_image()
        ):
            return _operation_result(
                (
                    f"Cannot attach image '{target.path}' ({image_size_bytes} bytes) - exceeds "
                    "chunking_max_image_mb_per_image "
                    f"({get_chunking_max_image_mb_per_image()} MB)."
                ),
                operation="read",
                path=target.path,
                status="unsupported",
                exists=True,
                error_type="image_too_large",
                metadata={
                    "media_mode": "image",
                    "size_bytes": image_size_bytes,
                    **target.requested_path_metadata,
                },
            )
        payload = build_image_tool_payload(
            image_path=target.full_path,
            vault_path=str(vault_path),
        )
        return _operation_result(
            [payload.note, payload.image_blob],
            operation="read",
            path=target.path,
            status="completed",
            exists=True,
            metadata={
                "media_mode": "image",
                **payload.metadata,
                **target.requested_path_metadata,
            },
        )

    try:
        file_content = _read_existing_text_file(target)
    except VaultFileOperationRejected as exc:
        return _rejected_result(exc, operation="read", fallback_path=target.path)

    if start_line > 0 or line_count > 0:
        return _slice_text_read_result(
            target=target,
            content=file_content,
            start_line=start_line,
            line_count=line_count,
        )

    if SUPPORTED_READ_FILE_TYPES.get(extension) == "markdown":
        return _markdown_read_result(
            target=target,
            vault_path=vault_path,
            file_content=file_content,
        )
    return _operation_result(
        file_content,
        operation="read",
        path=target.path,
        status="completed",
        exists=True,
        metadata={
            "media_mode": "text",
            "content_chars": len(file_content),
            **target.requested_path_metadata,
        },
    )


def _slice_text_read_result(
    *,
    target: VaultTextTarget,
    content: str,
    start_line: int,
    line_count: int,
) -> VaultFileOperationResult:
    lines = content.splitlines()
    start = max(start_line, 1) if start_line else 1
    count = line_count if line_count > 0 else max(len(lines) - start + 1, 0)
    selected = lines[start - 1 : start - 1 + count]
    media_mode = (
        "markdown"
        if SUPPORTED_READ_FILE_TYPES.get(target.full_path.suffix.lower()) == "markdown"
        else "text"
    )
    return _operation_result(
        "\n".join(selected),
        operation="read",
        path=target.path,
        status="completed",
        exists=True,
        metadata={
            "media_mode": media_mode,
            "content_chars": len(content),
            "start_line": start,
            "line_count": count,
            "lines_returned": len(selected),
            "total_lines": len(lines),
            **target.requested_path_metadata,
        },
    )


def _markdown_read_result(
    *,
    target: VaultTextTarget,
    vault_path: Path,
    file_content: str,
) -> VaultFileOperationResult:
    markdown_chunks = parse_markdown_chunks(file_content)
    if not any(chunk.kind == "image_ref" for chunk in markdown_chunks):
        return _operation_result(
            file_content,
            operation="read",
            path=target.path,
            status="completed",
            exists=True,
            metadata={
                "media_mode": "markdown",
                "content_chars": len(file_content),
                **target.requested_path_metadata,
            },
        )

    decision = evaluate_markdown_image_policy(
        file_content=file_content,
        markdown_chunks=markdown_chunks,
        source_markdown_path=target.path,
        vault_path=str(vault_path),
        auto_cache_max_tokens=get_auto_cache_max_tokens(),
        policy=default_chunking_policy(),
    )
    if not decision.attach_images:
        return _operation_result(
            decision.normalized_text or file_content,
            operation="read",
            path=target.path,
            status="completed",
            exists=True,
            metadata={
                "media_mode": "markdown",
                "content_chars": len(file_content),
                "image_attachments_skipped": True,
                "image_skip_reason": decision.reason,
                **target.requested_path_metadata,
            },
        )

    built = build_input_files_prompt(
        input_file_data=[
            {
                "filepath": target.path,
                "source_path": target.path,
                "filename": Path(target.path).stem,
                "content": file_content,
                "found": True,
                "error": None,
                "images_policy": "auto",
            }
        ],
        vault_path=str(vault_path),
        include_file_framing=False,
        supports_vision=None,
    )
    if isinstance(built.prompt, list):
        return _operation_result(
            built.prompt,
            operation="read",
            path=target.path,
            status="completed",
            exists=True,
            metadata={
                "filepath": target.path,
                "media_mode": "markdown+images",
                "attached_image_count": built.attached_image_count,
                "attached_image_bytes": built.attached_image_bytes,
                "warnings": built.warnings,
                **target.requested_path_metadata,
            },
        )
    return _operation_result(
        built.prompt_text,
        operation="read",
        path=target.path,
        status="completed",
        exists=True,
        metadata={
            "media_mode": "markdown",
            "content_chars": len(file_content),
            "attached_image_count": built.attached_image_count,
            "attached_image_bytes": built.attached_image_bytes,
            **target.requested_path_metadata,
        },
    )


def _parse_markdown_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---"):
        return {}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if closing_index is None:
        return {}
    parsed = yaml.safe_load("\n".join(lines[1:closing_index])) or {}
    return parsed if isinstance(parsed, dict) else {}


def _read_existing_text_file(target: VaultTextTarget) -> str:
    if not target.full_path.exists():
        raise VaultFileOperationRejected(
            "file_not_found",
            f"Vault file not found: {target.path}",
            details={"path": target.path, **target.requested_path_metadata},
        )
    if not target.full_path.is_file():
        raise VaultFileOperationRejected(
            "not_file",
            f"Vault path is not a file: {target.path}",
            details={"path": target.path, **target.requested_path_metadata},
        )
    try:
        return target.full_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise VaultFileOperationRejected(
            "file_not_text",
            f"Vault file is not UTF-8 text: {target.path}",
            details={"path": target.path, **target.requested_path_metadata},
        ) from exc


def _check_existing_file_hash(
    *,
    target: VaultTextTarget,
    expected_sha256: str | None,
) -> None:
    if not target.full_path.exists():
        raise VaultFileOperationRejected(
            "file_not_found",
            f"Vault file not found: {target.path}",
            details={"path": target.path, **target.requested_path_metadata},
        )
    if not target.full_path.is_file():
        raise VaultFileOperationRejected(
            "not_file",
            f"Vault path is not a file: {target.path}",
            details={"path": target.path, **target.requested_path_metadata},
        )
    if not expected_sha256:
        return
    current_sha256 = _sha256_file(target.full_path)
    if expected_sha256 != current_sha256:
        raise VaultFileOperationRejected(
            "file_conflict",
            f"File changed since expected hash was captured: {target.path}",
            details={
                "path": target.path,
                "expected_sha256": expected_sha256,
                "actual_sha256": current_sha256,
                **target.requested_path_metadata,
            },
        )


def _check_expected_sha256(
    *,
    target: VaultTextTarget,
    current: str,
    expected_sha256: str | None,
) -> None:
    if not expected_sha256:
        return
    current_sha256 = _sha256_text(current)
    if expected_sha256 != current_sha256:
        raise VaultFileOperationRejected(
            "file_conflict",
            f"File changed since expected hash was captured: {target.path}",
            details={
                "path": target.path,
                "expected_sha256": expected_sha256,
                "actual_sha256": current_sha256,
                **target.requested_path_metadata,
            },
        )


def _sha256_text(content: str) -> str:
    return hash_file_content(content, length=None)


def _sha256_file(path: Path) -> str:
    return hash_file_bytes(path, length=None)


def _operation_result(
    return_value: Any,
    *,
    operation: str,
    path: str = "",
    destination: str = "",
    search_term: str = "",
    status: str = "completed",
    exists: bool | None = None,
    error_type: str | None = None,
    content: Any | None = None,
    metadata: dict[str, Any] | None = None,
) -> VaultFileOperationResult:
    payload: dict[str, Any] = {
        "status": status,
        "operation": operation,
    }
    if path:
        payload["path"] = path
    if destination:
        payload["destination"] = destination
    if search_term:
        payload["search_term"] = search_term
    if exists is not None:
        payload["exists"] = exists
    if error_type:
        payload["error_type"] = error_type
    if metadata:
        payload.update(metadata)
    return VaultFileOperationResult(
        return_value=return_value, content=content, metadata=payload
    )


def _default_list_max_results() -> int:
    try:
        return get_file_list_max_results()
    except Exception:
        return 200


def _default_search_timeout_seconds() -> float:
    try:
        return get_file_search_timeout_seconds()
    except Exception:
        return 10


def _mutation_result(
    return_value: str,
    *,
    operation: str,
    path: str,
    mutation: RecordedMutationResult,
    exists: bool,
    metadata: dict[str, Any] | None = None,
) -> VaultFileOperationResult:
    return _operation_result(
        return_value,
        operation=operation,
        path=path,
        status="completed",
        exists=exists,
        metadata={
            "task_id": mutation.task_id,
            "vault_id": mutation.vault_id,
            **(metadata or {}),
        },
    )


def _rejected_result(
    exc: VaultFileOperationRejected,
    *,
    operation: str,
    fallback_path: str,
) -> VaultFileOperationResult:
    path = str(exc.details.get("path") or normalize_vault_relative_path(fallback_path))
    status_by_code = {
        "file_not_found": "not_found",
        "not_file": "invalid_target",
        "file_not_text": "unsupported",
        "text_not_found": "invalid_target",
        "invalid_count": "error",
    }
    return _operation_result(
        str(exc),
        operation=operation,
        path=path,
        status=status_by_code.get(exc.code, "error"),
        exists=False if exc.code == "file_not_found" else None,
        error_type=exc.code,
        metadata=exc.details,
    )


def _delete_empty_directory_operation(
    *,
    vault_path: str | Path,
    path: str,
) -> VaultFileOperationResult:
    try:
        cleanup = delete_empty_vault_directory_tree(vault_path=vault_path, path=path)
    except VaultMutationRejected as exc:
        status = "not_found" if exc.code == "directory_not_found" else "invalid_target"
        return _operation_result(
            str(exc),
            operation="delete",
            path=path,
            status=status,
            exists=exc.code != "directory_not_found",
            error_type=exc.code,
        )
    return _directory_cleanup_result(
        vault_path=vault_path,
        path=path,
        cleanup=cleanup,
    )


def _directory_cleanup_result(
    *,
    vault_path: str | Path,
    path: str,
    cleanup: DirectoryCleanupResult,
) -> VaultFileOperationResult:
    removed = list(cleanup.removed_paths)
    skipped = list(cleanup.skipped_paths)
    blockers = list(cleanup.blocker_paths)
    if skipped:
        message = (
            f"Removed {len(removed)} empty directories under '{path}'. "
            f"Skipped {len(skipped)} non-empty directories: " + ", ".join(skipped)
        )
        status = "partial"
    elif removed:
        message = f"Removed {len(removed)} empty directories under '{path}'"
        status = "completed"
    else:
        message = f"No empty directories were removed under '{path}'"
        status = "partial"
    if removed:
        record_vault_directory_mutation(
            vault_path=vault_path,
            path=path,
            operation="delete",
            before_exists=True,
            after_exists=cleanup.after_exists,
            event_sequence=cleanup.event_sequence,
            metadata={
                "removed_directory_count": len(removed),
                "skipped_directory_count": len(skipped),
            },
        )
    return _operation_result(
        message,
        operation="delete",
        path=path,
        status=status,
        exists=cleanup.after_exists,
        metadata={
            "target_type": "directory",
            "removed_directories": removed,
            "skipped_non_empty_directories": skipped,
            "remaining_directory_contents": blockers,
            "removed_count": len(removed),
            "skipped_count": len(skipped),
            "remaining_content_count": len(blockers),
            "task_id": cleanup.task_id,
            "vault_id": cleanup.vault_id,
            "event_sequence": cleanup.event_sequence,
        },
    )


def _collect_limited_matches(
    *,
    pattern: Path,
    recursive: bool,
    root_path: Path,
    max_results: int,
) -> tuple[list[str], list[str], int, int]:
    selected_files: list[str] = []
    selected_directories: list[str] = []
    total_files = 0
    total_directories = 0
    root = root_path.resolve()
    for match in glob.iglob(str(pattern), recursive=recursive):
        candidate = Path(match)
        try:
            resolved = candidate.resolve()
            _ensure_within_root(root, resolved)
        except (OSError, VaultFileOperationRejected):
            continue
        relative = _relative_to_root(root, resolved)
        if not relative or any(part.startswith(".") for part in relative.split("/")):
            continue
        if resolved.is_dir():
            total_directories += 1
            _keep_sorted_candidate(selected_directories, relative, max_results)
        else:
            total_files += 1
            _keep_sorted_candidate(selected_files, relative, max_results)
    if max_results > 0 and total_files + total_directories > max_results:
        directories = selected_directories[:max_results]
        file_slots = max(0, max_results - min(total_directories, max_results))
        return selected_files[:file_slots], directories, total_files, total_directories
    return selected_files, selected_directories, total_files, total_directories


def _keep_sorted_candidate(candidates: list[str], value: str, max_results: int) -> None:
    candidates.append(value)
    candidates.sort()
    if max_results > 0 and len(candidates) > max_results:
        candidates.pop()


def _empty_directory_candidates(
    *,
    vault_path: Path,
    directories: list[str],
    scope_relative_path: str | None,
    include_scope_when_empty: bool,
) -> list[str]:
    candidate_paths: list[str] = []
    if include_scope_when_empty and scope_relative_path:
        candidate_paths.append(scope_relative_path)
    candidate_paths.extend(directories)
    empty_paths = [
        candidate
        for candidate in candidate_paths
        if candidate and _directory_tree_has_no_files(vault_path, candidate)
    ]
    selected: list[str] = []
    for candidate in sorted(empty_paths, key=lambda item: (item.count("/"), item)):
        if any(
            candidate == parent or candidate.startswith(f"{parent}/")
            for parent in selected
        ):
            continue
        selected.append(candidate)
    return sorted(selected)


def _directory_tree_has_no_files(vault_path: Path, relative_path: str) -> bool:
    full_path = vault_path / relative_path
    if not full_path.is_dir():
        return False
    for _root, _dirs, files in os.walk(full_path):
        if files:
            return False
    return True


def _read_virtual_file_operation(
    *,
    path: str,
    start_line: int,
    line_count: int,
) -> VaultFileOperationResult:
    mount_key = _virtual_mount_key(path) or ""
    root = _virtual_mount_root(mount_key)
    rel = path.strip().lstrip("./")[len(mount_key) :].lstrip("/")
    if not rel:
        return _operation_result(
            f"Cannot read '{path}' - this is a directory, not a file.",
            operation="read",
            path=path,
            status="invalid_target",
            exists=True,
            error_type="is_directory",
        )
    if "." in Path(rel).name and not rel.endswith(".md"):
        return _operation_result(
            "Only .md files are allowed in virtual mounts.",
            operation="read",
            path=path,
            status="unsupported",
            error_type="unsupported_file_type",
        )
    if "." not in Path(rel).name:
        rel = f"{rel}.md"
    full_path = (root / rel).resolve()
    try:
        _ensure_within_root(root, full_path)
    except VaultFileOperationRejected as exc:
        return _rejected_result(exc, operation="read", fallback_path=path)
    if full_path.is_dir():
        return _operation_result(
            f"Cannot read '{path}' - this is a directory, not a file.",
            operation="read",
            path=path,
            status="invalid_target",
            exists=True,
            error_type="is_directory",
        )
    if not full_path.exists():
        return _operation_result(
            f"Cannot read '{path}' - file does not exist.",
            operation="read",
            path=path,
            status="not_found",
            exists=False,
            error_type="file_not_found",
        )
    content = full_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    metadata: dict[str, Any] = {
        "media_mode": "markdown",
        "content_chars": len(content),
        "virtual_mount": mount_key,
    }
    return_value = content
    if start_line > 0 or line_count > 0:
        start = max(start_line, 1) if start_line else 1
        count = line_count if line_count > 0 else len(lines) - start + 1
        selected = lines[start - 1 : start - 1 + count]
        return_value = "\n".join(selected)
        metadata.update(
            {
                "start_line": start,
                "line_count": count,
                "lines_returned": len(selected),
                "total_lines": len(lines),
            }
        )
    return _operation_result(
        return_value,
        operation="read",
        path=path,
        status="completed",
        exists=True,
        metadata=metadata,
    )


def _list_virtual_paths_operation(
    *,
    path: str,
    recursive: bool,
    max_results: int,
) -> VaultFileOperationResult:
    mount_key = _virtual_mount_key(path) or ""
    root = _virtual_mount_root(mount_key)
    rel = path.strip().lstrip("./")[len(mount_key) :].lstrip("/")
    if ".." in rel.split("/"):
        raise VaultFileOperationRejected(
            "invalid_path",
            "Path cannot contain '..' for virtual mounts.",
            details={"path": path},
        )
    pattern_path = rel
    if not pattern_path:
        pattern_path = "**/*" if recursive else "*"
    elif not any(ch in pattern_path for ch in "*?["):
        candidate = (root / pattern_path).resolve()
        _ensure_within_root(root, candidate)
        if candidate.is_dir():
            pattern_path = str(Path(pattern_path) / ("**/*" if recursive else "*"))
    files, directories, file_count, directory_count = _collect_limited_matches(
        pattern=root / pattern_path,
        recursive=recursive or "**" in pattern_path,
        root_path=root,
        max_results=max_results,
    )
    truncated = max_results > 0 and file_count + directory_count > max_results
    prefixed_files = [f"{mount_key}/{item}" for item in files]
    prefixed_directories = [f"{mount_key}/{item}" for item in directories]
    if file_count == 0 and directory_count == 0:
        message = f"No files or directories found for path '{path}'"
    else:
        parts = []
        if prefixed_directories:
            parts.append(
                f"Directories ({len(prefixed_directories)}):\n"
                + "\n".join(f"  {directory}/" for directory in prefixed_directories)
            )
        if prefixed_files:
            parts.append(
                f"Files ({len(prefixed_files)}):\n"
                + "\n".join(f"  {file_path}" for file_path in prefixed_files)
            )
        if truncated:
            parts.append(
                f"... truncated to {max_results} results. Narrow your path or disable recursion."
            )
        message = "\n\n".join(parts)
    return _operation_result(
        message,
        operation="list",
        path=path or mount_key,
        status="completed",
        exists=True,
        metadata={
            "directory_count": len(prefixed_directories),
            "file_count": len(prefixed_files),
            "directories": prefixed_directories,
            "files": prefixed_files,
            "empty_directory_candidates": [],
            "empty_directory_candidate_count": 0,
            "truncated": truncated,
            "virtual_mount": mount_key,
        },
    )


def _resolve_search_roots(root: Path, scope: str) -> list[Path]:
    """Resolve a bounded file, directory, or glob scope for ripgrep."""
    if not scope:
        return [root]
    candidate = (root / scope).resolve()
    _ensure_within_root(root, candidate)
    if candidate.exists():
        return [candidate]

    matches: list[Path] = []
    for raw_match in glob.iglob(str(root / scope), recursive="**" in scope):
        match = Path(raw_match).resolve()
        try:
            _ensure_within_root(root, match)
        except VaultFileOperationRejected:
            continue
        relative = _relative_to_root(root, match)
        if any(part.startswith(".") for part in Path(relative).parts):
            continue
        matches.append(match)
    return sorted(dict.fromkeys(matches))


def _format_rg_matches(
    output: str, vault_root: Path, *, result_prefix: str = ""
) -> list[str]:
    matches: list[str] = []
    for line in output.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        file_path, line_number, text = parts
        try:
            relative = _relative_to_root(vault_root, Path(file_path).resolve())
        except VaultFileOperationRejected:
            continue
        if result_prefix:
            relative = f"{result_prefix}/{relative}"
        matches.append(f"{relative}:{line_number}: {text}")
    return matches


def _virtual_mount_key(path: str) -> str | None:
    if not path:
        return None
    mount_key = path.strip().lstrip("./").split("/", 1)[0]
    return mount_key if mount_key in VIRTUAL_MOUNTS else None


def _virtual_mount_root(mount_key: str) -> Path:
    mount = VIRTUAL_MOUNTS.get(mount_key)
    if not mount:
        raise VaultFileOperationRejected(
            "invalid_virtual_mount",
            f"Unknown virtual mount: {mount_key}",
            details={"path": mount_key},
        )
    return Path(str(mount["root"])).resolve()


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise VaultFileOperationRejected(
            "path_escapes_vault",
            "Path escapes vault boundaries.",
        ) from exc


def _ensure_within_root(root: Path, path: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise VaultFileOperationRejected(
            "path_escapes_vault",
            "Path escapes vault boundaries.",
        ) from exc


def _reject_virtual_mount_path(path: str) -> None:
    if not path:
        return
    mount_key = path.strip().lstrip("./").split("/", 1)[0]
    if mount_key in VIRTUAL_MOUNTS:
        raise VaultFileOperationRejected(
            "virtual_mount_read_only",
            f"'{mount_key}' is reserved for a virtual mount",
            details={"path": path},
        )


def _should_try_markdown_file(path: str) -> bool:
    if not path or path in {".", "/"} or path.endswith("/"):
        return False
    return "." not in Path(path).name


def _edit_details(replacement: TextReplacement) -> dict[str, str]:
    if replacement.edit_id:
        return {"edit_id": replacement.edit_id}
    return {}


def _edit_message(replacement: TextReplacement, suffix: str) -> str:
    if replacement.edit_id:
        return f"Edit {replacement.edit_id} {suffix}"
    return f"Edit {suffix}"
