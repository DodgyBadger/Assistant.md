"""
Shared utilities for file operations tools.

Provides security validation and path resolution for all file operations.
"""

import os
from pathlib import Path

import tiktoken

from core.constants import VIRTUAL_MOUNTS


def _normalize_virtual_path(path: str) -> str:
    return path.strip().lstrip("./")


def get_virtual_mount_key(path: str) -> str | None:
    """Return virtual mount key if path targets a virtual mount."""
    if not path:
        return None
    normalized = _normalize_virtual_path(path)
    prefix = normalized.split("/", 1)[0]
    if prefix in VIRTUAL_MOUNTS:
        return prefix
    return None


def is_virtual_docs_path(path: str) -> bool:
    """Return True if the path targets the virtual docs mount."""
    return get_virtual_mount_key(path) == "__virtual_docs__"


def resolve_virtual_path(path: str) -> tuple[str, dict[str, object]]:
    """Resolve a virtual mount path to an absolute path and mount metadata."""
    mount_key = get_virtual_mount_key(path)
    if not mount_key:
        raise ValueError("Not a virtual mount path")

    mount = VIRTUAL_MOUNTS[mount_key]
    root_value = mount.get("root")
    if not isinstance(root_value, str):
        raise ValueError(f"Virtual mount '{mount_key}' has no valid root")
    root = Path(root_value).resolve()

    normalized = _normalize_virtual_path(path)
    rel = normalized[len(mount_key) :].lstrip("/")

    if ".." in rel.split(os.sep):
        raise ValueError("Path traversal not allowed in virtual mount path")

    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("Virtual mount path escapes root") from exc

    return str(candidate), mount


def resolve_virtual_docs_path(path: str) -> str:
    """Resolve a virtual docs path to an absolute path under the docs root."""
    resolved, _ = resolve_virtual_path(path)
    return resolved


def validate_and_resolve_path(
    path: str, vault_path: str, *, markdown_only: bool = True
) -> str:
    """Validate path and resolve to full path within vault boundaries.

    Args:
        path: Relative file path to validate
        vault_path: Root vault directory path
        markdown_only: Require .md extension when the path includes a file extension

    Returns:
        Absolute resolved path within vault

    Raises:
        ValueError: If path fails security validation
    """
    # Security validations
    mount_key = get_virtual_mount_key(path)
    if mount_key:
        raise ValueError(f"'{mount_key}' is reserved for a virtual mount")
    if ".." in path:
        raise ValueError("Path traversal not allowed - '..' found in path")

    if path.startswith("/"):
        raise ValueError("Absolute paths not allowed")

    # Markdown extension enforcement
    if markdown_only and "." in os.path.basename(path):
        if not path.endswith(".md"):
            raise ValueError(
                "Only .md files are allowed. Please use '.md' extension for all files."
            )

    # Resolve to full path.
    # Use realpath to collapse symlinks before boundary checks.
    full_path = os.path.join(vault_path, path)
    resolved_path = os.path.realpath(full_path)
    vault_abs = os.path.realpath(vault_path)

    # Ensure the resolved path is within vault boundaries after symlink resolution.
    if not resolved_path.startswith(vault_abs + os.sep) and resolved_path != vault_abs:
        raise ValueError("Path escapes vault boundaries")

    return resolved_path


def estimate_token_count(text: str, encoding_name: str = "cl100k_base") -> int:
    """
    Estimate token count for text using tiktoken.

    Args:
        text: The text content to count tokens for
        encoding_name: The tiktoken encoding to use (default: cl100k_base for GPT-4/Claude-like models)

    Returns:
        Estimated token count

    Note:
        Uses cl100k_base encoding by default, which is appropriate for:
        - GPT-4, GPT-4o, GPT-3.5-turbo
        - Claude models (approximate)
        - Most modern LLMs
    """
    encoding = tiktoken.get_encoding(encoding_name)
    return len(encoding.encode(text))
