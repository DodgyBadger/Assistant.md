"""Final shared SQLite schema and transaction owner for access state."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from core.database import connect_sqlite_from_system_db
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations

DB_NAME = "access"
MIGRATION_NAMESPACE = "access"
ACCESS_MIGRATIONS = (
    SQLiteMigration(
        version=1,
        name="principal_owned_access_state",
        apply=lambda c: _create_schema(c),
    ),
)


def connect_access(system_root: str | None = None) -> sqlite3.Connection:
    """Open access.db with the common durability and concurrency policy."""
    conn = connect_sqlite_from_system_db(DB_NAME, system_root)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


@contextmanager
def write_transaction(system_root: str | None = None) -> Iterator[sqlite3.Connection]:
    """Own one short-lived immediate transaction across access domains."""
    conn = connect_access(system_root)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def ensure_access_schema(
    system_root: str | None = None, *, apply_migrations: bool = False
) -> None:
    """Create the final access schema without importing development databases."""
    conn = connect_access(system_root)
    try:
        _create_schema(conn)
        if apply_migrations:
            apply_sqlite_migrations(
                conn, namespace=MIGRATION_NAMESPACE, migrations=ACCESS_MIGRATIONS
            )
        conn.commit()
    finally:
        conn.close()


def _create_schema(conn: sqlite3.Connection) -> None:
    # Domain builders contain schema only; this module owns ordering and commits.
    from core.connections.schema import create_connections_schema
    from core.mcp.schema import create_mcp_schema
    from core.secrets.schema import create_secrets_schema

    create_secrets_schema(conn)
    create_connections_schema(conn)
    create_mcp_schema(conn)
