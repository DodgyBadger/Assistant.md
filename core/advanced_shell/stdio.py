"""Structured advanced-shell stdio launch contract shared by MCP producers."""

from __future__ import annotations

import base64
import json
import re
from pathlib import PurePosixPath

STRUCTURED_STDIO_PREFIX = "assistantmd-stdio-v1:"
MAX_STRUCTURED_STDIO_ENVELOPE_BYTES = 64 * 1024
MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_ENVIRONMENT_VALUES = 16
MAX_ENVIRONMENT_VALUE_BYTES = 4096
MAX_ROOTS = 16
ALLOWED_PATH_ROOTS = (
    PurePosixPath("/workspace"),
    PurePosixPath("/home/advanced-shell"),
)
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
RESERVED_ENVIRONMENT_NAMES = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "NPM_CONFIG_PREFIX",
        "SHELL",
        "TMPDIR",
        "UV_TOOL_BIN_DIR",
        "UV_TOOL_DIR",
    }
)


def validate_executable(value: str) -> str:
    """Normalize one absolute advanced-shell executable path."""
    normalized = str(value or "").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or ".." in path.parts
        or not path.is_absolute()
    ):
        raise ValueError("Advanced-shell stdio executable must be an absolute path.")
    return normalized


def validate_arguments(values: tuple[str, ...]) -> tuple[str, ...]:
    """Validate bounded literal argv values."""
    if len(values) > MAX_ARGUMENTS or any("\x00" in value for value in values):
        raise ValueError("Advanced-shell stdio arguments are invalid.")
    if sum(len(value.encode()) for value in values) > MAX_ARGUMENT_BYTES:
        raise ValueError("Advanced-shell stdio arguments exceed the size limit.")
    return values


def validate_advanced_shell_path(value: str, *, label: str) -> str:
    """Validate one absolute path below a supported advanced-shell root."""
    normalized = str(value or "").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or ".." in path.parts
        or not path.is_absolute()
        or not any(
            path == root or path.is_relative_to(root) for root in ALLOWED_PATH_ROOTS
        )
    ):
        raise ValueError(f"Advanced-shell stdio {label} is outside allowed roots.")
    return normalized


def validate_environment(
    values: tuple[tuple[str, str], ...],
) -> tuple[tuple[str, str], ...]:
    """Validate and sort a bounded non-secret environment mapping."""
    if len(values) > MAX_ENVIRONMENT_VALUES:
        raise ValueError("Advanced-shell stdio environment has too many entries.")
    result: dict[str, str] = {}
    for name, value in values:
        if (
            ENVIRONMENT_NAME_PATTERN.fullmatch(name) is None
            or name in RESERVED_ENVIRONMENT_NAMES
            or "\x00" in value
            or len(value.encode()) > MAX_ENVIRONMENT_VALUE_BYTES
        ):
            raise ValueError("Advanced-shell stdio environment is invalid.")
        if name in result:
            raise ValueError("Advanced-shell stdio environment names must be unique.")
        result[name] = value
    return tuple(sorted(result.items()))


def encode_structured_launch(
    *,
    executable: str,
    arguments: tuple[str, ...],
    working_directory: str,
    environment: tuple[tuple[str, str], ...],
) -> str:
    """Encode one already-validated launch for the forced-command wrapper."""
    payload = json.dumps(
        {
            "executable": executable,
            "args": list(arguments),
            "cwd": working_directory,
            "env": dict(environment),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if len(payload) > MAX_STRUCTURED_STDIO_ENVELOPE_BYTES:
        raise ValueError("Advanced-shell stdio launch exceeds the size limit.")
    return STRUCTURED_STDIO_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii")
