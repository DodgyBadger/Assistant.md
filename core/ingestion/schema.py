"""SQLite schema and migrations for durable ingestion jobs."""

from __future__ import annotations

import sqlite3

from core.database import connect_sqlite_from_system_db
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations

DB_NAME = "ingestion_jobs"
MIGRATION_NAMESPACE = "ingestion_jobs"

INGESTION_JOB_MIGRATIONS = (
    SQLiteMigration(
        version=1,
        name="add_strategy_provenance",
        apply=lambda conn: _ensure_ingestion_jobs_table(conn),
    ),
)


def ensure_ingestion_jobs_schema(
    system_root: str | None = None,
    *,
    apply_migrations: bool = False,
) -> None:
    """Create or migrate the ingestion job table."""
    conn = connect_sqlite_from_system_db(DB_NAME, system_root)
    try:
        if apply_migrations:
            apply_sqlite_migrations(
                conn,
                namespace=MIGRATION_NAMESPACE,
                migrations=INGESTION_JOB_MIGRATIONS,
            )
        elif not _table_exists(conn, "ingestion_jobs"):
            _create_ingestion_jobs_table(conn)
        conn.commit()
    finally:
        conn.close()


def _ensure_ingestion_jobs_table(conn: sqlite3.Connection) -> None:
    _create_ingestion_jobs_table(conn)
    for column_name, definition in (
        ("selected_strategy", "VARCHAR"),
        ("selected_provider", "VARCHAR"),
        ("selected_model", "VARCHAR"),
        ("strategy_attempts", "JSON"),
        ("fallback_reason", "TEXT"),
    ):
        _ensure_column(conn, column_name, definition)


def _create_ingestion_jobs_table(conn: sqlite3.Connection) -> None:
    """Create the current table for a new database without migrating old tables."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_uri VARCHAR NOT NULL,
            vault VARCHAR,
            source_type VARCHAR NOT NULL,
            mime_hint VARCHAR,
            options JSON,
            status VARCHAR NOT NULL,
            error TEXT,
            outputs JSON,
            selected_strategy VARCHAR,
            selected_provider VARCHAR,
            selected_model VARCHAR,
            strategy_attempts JSON,
            fallback_reason TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _ensure_column(
    conn: sqlite3.Connection,
    column_name: str,
    definition: str,
) -> None:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(ingestion_jobs)")}
    if column_name not in columns:
        conn.execute(
            f"ALTER TABLE ingestion_jobs ADD COLUMN {column_name} {definition}"
        )
