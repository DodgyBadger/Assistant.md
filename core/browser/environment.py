"""Explicit runtime-only child environment for untrusted browser work."""

from __future__ import annotations

import os
from pathlib import Path

_RUNTIME_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LC_MESSAGES",
        "TZ",
        "PLAYWRIGHT_BROWSERS_PATH",
    }
)


def browser_environment(
    *, display: str | None = None, temporary_directory: Path | None = None
) -> dict[str, str]:
    environment = {
        key: value
        for key in _RUNTIME_KEYS
        if (value := os.environ.get(key)) is not None
    }
    if temporary_directory is not None:
        environment.update(
            {key: str(temporary_directory) for key in ("TMPDIR", "TMP", "TEMP")}
        )
    if display is not None:
        environment["DISPLAY"] = display
    return environment
