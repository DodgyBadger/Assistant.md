"""Durable path bookmarks, independent of manifest refresh and retention."""

from contextlib import closing
from pathlib import Path

from core.database import connect_sqlite_from_system_db
from core.identity import require_current_execution_authority
from core.vault_state.identity import resolve_or_create_vault_identity
from core.vault_state.pathing import resolve_vault_relative_path


def favorite_paths(vault_root: Path) -> set[str]:
    """Return the current principal's existing, visible bookmarked paths."""
    principal = require_current_execution_authority().principal_id
    vault_id = resolve_or_create_vault_identity(vault_root).vault_id
    with closing(connect_sqlite_from_system_db("vault_state")) as conn:
        rows = conn.execute(
            "SELECT path FROM vault_favorites WHERE principal_id = ? AND vault_id = ?",
            (principal, vault_id),
        ).fetchall()
    paths = set()
    for (path,) in rows:
        try:
            resolved = resolve_vault_relative_path(vault_path=vault_root, path=path)
        except ValueError:
            continue
        if not any(part.startswith(".") for part in Path(path).parts) and (
            resolved.is_file() or resolved.is_dir()
        ):
            paths.add(str(path))
    return paths


def set_favorite_paths(vault_root: Path, paths: list[str], favorite: bool) -> int:
    """Apply prevalidated explicit bookmark states in one transaction."""
    principal = require_current_execution_authority().principal_id
    vault_id = resolve_or_create_vault_identity(vault_root).vault_id
    with closing(connect_sqlite_from_system_db("vault_state")) as conn, conn:
        before = conn.total_changes
        for path in paths:
            if favorite:
                conn.execute(
                    "INSERT OR IGNORE INTO vault_favorites (principal_id, vault_id, path) VALUES (?, ?, ?)",
                    (principal, vault_id, path),
                )
            else:
                conn.execute(
                    "DELETE FROM vault_favorites WHERE principal_id = ? AND vault_id = ? AND path = ?",
                    (principal, vault_id, path),
                )
        return conn.total_changes - before


def move_favorite_paths(vault_root: Path, source: str, destination: str) -> None:
    """Follow a successful move for every principal, including descendants."""
    vault_id = resolve_or_create_vault_identity(vault_root).vault_id
    with closing(connect_sqlite_from_system_db("vault_state")) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT principal_id, path FROM vault_favorites WHERE vault_id = ?",
            (vault_id,),
        ).fetchall()
        for principal, path in rows:
            if path != source and not path.startswith(source + "/"):
                continue
            target = destination + path[len(source) :]
            conn.execute(
                "DELETE FROM vault_favorites WHERE principal_id = ? AND vault_id = ? AND path = ?",
                (principal, vault_id, path),
            )
            conn.execute(
                "INSERT OR IGNORE INTO vault_favorites (principal_id, vault_id, path) VALUES (?, ?, ?)",
                (principal, vault_id, target),
            )


def remove_favorite_paths(vault_root: Path, paths: list[str]) -> None:
    """Forget deleted exact paths for every principal; restores start unbookmarked."""
    if not paths:
        return
    vault_id = resolve_or_create_vault_identity(vault_root).vault_id
    with closing(connect_sqlite_from_system_db("vault_state")) as conn, conn:
        conn.executemany(
            "DELETE FROM vault_favorites WHERE vault_id = ? AND path = ?",
            [(vault_id, path) for path in paths],
        )
