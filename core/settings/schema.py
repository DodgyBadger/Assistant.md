"""Schema owned by the installation settings subsystem."""

from __future__ import annotations

import sqlite3

from core.database import connect_sqlite_from_system_db
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations

DB_NAME = "settings"
MIGRATION_NAMESPACE = "settings"
SECTIONS = ("settings", "models", "providers", "tools")


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS configuration_state "
        "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
        "revision INTEGER NOT NULL, source TEXT NOT NULL)"
    )
    for section in SECTIONS:
        # Table identifiers come only from the fixed subsystem-owned tuple.
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS configuration_{section} "
            "(name TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)))"
        )


SETTINGS_MIGRATIONS = (
    SQLiteMigration(1, "create_configuration_records", _create_tables),
)


def ensure_settings_schema(system_root: str | None = None) -> None:
    conn = connect_sqlite_from_system_db(DB_NAME, system_root)
    try:
        apply_sqlite_migrations(
            conn, namespace=MIGRATION_NAMESPACE, migrations=SETTINGS_MIGRATIONS
        )
        conn.commit()
    finally:
        conn.close()
