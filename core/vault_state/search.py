"""Bounded literal content search within one vault directory."""

from __future__ import annotations

import json
import select
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from core.vault_state.pathing import resolve_vault_relative_path


class VaultContentSearchError(Exception):
    """A stable content-search failure suitable for API translation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class VaultContentMatch:
    path: str
    line: int
    column: int
    snippet: str


@dataclass(frozen=True)
class VaultContentSearchResult:
    matches: tuple[VaultContentMatch, ...]
    truncated: bool


def search_vault_content(
    *,
    vault_path: str | Path,
    query: str,
    path: str = "",
    limit: int = 100,
    timeout_seconds: float = 5.0,
) -> VaultContentSearchResult:
    """Return structured case-insensitive literal matches from text files."""
    normalized_query = query.strip()
    if not normalized_query:
        raise VaultContentSearchError("missing_query", "Search query is required.")
    vault_root = Path(vault_path).resolve()
    try:
        search_root = (
            resolve_vault_relative_path(
                vault_path=vault_root,
                path=path,
                markdown_only=False,
            )
            if path
            else vault_root
        )
    except ValueError as exc:
        raise VaultContentSearchError(
            "invalid_scope", "Search scope must be a safe vault-relative folder."
        ) from exc
    if not search_root.exists() or not search_root.is_dir():
        raise VaultContentSearchError(
            "invalid_scope", "Search scope must be an existing vault folder."
        )
    bounded_limit = min(max(int(limit), 1), 200)
    command = [
        "rg",
        "--json",
        "--fixed-strings",
        "--ignore-case",
        "--max-columns",
        "500",
        "--max-filesize",
        "2M",
        normalized_query,
        str(search_root),
    ]
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed executable and argv only
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise VaultContentSearchError(
            "ripgrep_not_found", "ripgrep is unavailable on this host."
        ) from exc

    matches: list[VaultContentMatch] = []
    truncated = False
    deadline = time.monotonic() + max(float(timeout_seconds), 0.1)
    try:
        if process.stdout is None:
            raise VaultContentSearchError(
                "search_failed", "Search output is unavailable."
            )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise VaultContentSearchError("timeout", "Content search timed out.")
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            if not ready:
                raise VaultContentSearchError("timeout", "Content search timed out.")
            line = process.stdout.readline()
            if not line:
                break
            match = _parse_match(line=line, vault_root=vault_root)
            if match is None:
                continue
            if len(matches) >= bounded_limit:
                truncated = True
                break
            matches.append(match)
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)

    if not truncated and process.returncode not in {0, 1, -15}:
        stderr = process.stderr.read().strip() if process.stderr else ""
        raise VaultContentSearchError(
            "search_failed", stderr or "Content search failed."
        )
    return VaultContentSearchResult(matches=tuple(matches), truncated=truncated)


def _parse_match(*, line: str, vault_root: Path) -> VaultContentMatch | None:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    if event.get("type") != "match":
        return None
    data = event.get("data") or {}
    raw_path = ((data.get("path") or {}).get("text") or "").strip()
    if not raw_path:
        return None
    try:
        relative = Path(raw_path).resolve().relative_to(vault_root).as_posix()
    except ValueError:
        return None
    submatches = data.get("submatches") or []
    first = submatches[0] if submatches else {}
    snippet = str((data.get("lines") or {}).get("text") or "").strip()
    return VaultContentMatch(
        path=relative,
        line=max(int(data.get("line_number") or 1), 1),
        column=max(int(first.get("start") or 0) + 1, 1),
        snippet=snippet[:500],
    )
