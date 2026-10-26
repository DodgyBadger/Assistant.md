"""Small SQLite migration runner for system databases."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SQLiteMigration:
    """One ordered migration for a SQLite database namespace."""

    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class SQLiteMigrationResult:
    """Summary of a migration run."""

    namespace: str
    applied: tuple[int, ...]
    skipped: tuple[int, ...]


def apply_sqlite_migrations(
    conn: sqlite3.Connection,
    *,
    namespace: str,
    migrations: Sequence[SQLiteMigration],
) -> SQLiteMigrationResult:
    """Apply ordered SQLite migrations once per namespace.

    Migration state is stored in the target database so each SQLite file tracks
    its own upgrade history. Each migration runs in its own transaction and is
    recorded only after its apply function completes.
    """
    normalized_namespace = str(namespace or "").strip()
    if not normalized_namespace:
        raise ValueError("migration namespace is required")

    ordered = _validate_migrations(migrations)
    _ensure_migrations_table(conn)

    applied_versions = _applied_versions(conn, namespace=normalized_namespace)
    applied: list[int] = []
    skipped: list[int] = []

    for migration in ordered:
        if migration.version in applied_versions:
            skipped.append(migration.version)
            continue

        with conn:
            migration.apply(conn)
            conn.execute(
                """
                INSERT INTO schema_migrations (namespace, version, name)
                VALUES (?, ?, ?)
                """,
                (normalized_namespace, migration.version, migration.name),
            )
        applied_versions.add(migration.version)
        applied.append(migration.version)

    return SQLiteMigrationResult(
        namespace=normalized_namespace,
        applied=tuple(applied),
        skipped=tuple(skipped),
    )


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            namespace TEXT NOT NULL,
            version INTEGER NOT NULL,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (namespace, version)
        )
        """)


def _applied_versions(conn: sqlite3.Connection, *, namespace: str) -> set[int]:
    rows = conn.execute(
        """
        SELECT version
        FROM schema_migrations
        WHERE namespace = ?
        """,
        (namespace,),
    ).fetchall()
    return {int(row[0]) for row in rows}


def _validate_migrations(
    migrations: Sequence[SQLiteMigration],
) -> tuple[SQLiteMigration, ...]:
    ordered = tuple(sorted(migrations, key=lambda migration: migration.version))
    seen_versions: set[int] = set()
    for migration in ordered:
        if migration.version <= 0:
            raise ValueError("migration versions must be positive integers")
        if migration.version in seen_versions:
            raise ValueError(f"duplicate migration version: {migration.version}")
        seen_versions.add(migration.version)
        if not str(migration.name or "").strip():
            raise ValueError(f"migration {migration.version} name is required")
    return ordered
