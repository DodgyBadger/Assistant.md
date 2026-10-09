"""One managed retirement migration for legacy derived session-summary data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from core.database import get_system_database_path
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations
from core.logger import UnifiedLogger

DB_NAME = "session_summaries"
MIGRATION_NAMESPACE = "session_summaries"
_RETIRED_TABLES = (
    "session_summaries_fts",
    "session_summary_field_vectors",
    "session_summary_artifacts",
    "session_summaries",
    "workstream_artifacts",
    "workstream_sessions",
    "workstream_field_vectors",
    "workstreams",
)
logger = UnifiedLogger(tag="session-summary-retirement")


def _retire_tables(conn: sqlite3.Connection) -> None:
    # DROP statements must share the runner's bookkeeping transaction.
    if not conn.in_transaction:
        conn.execute("BEGIN")
    for table in _RETIRED_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")


SESSION_SUMMARY_RETIREMENT_MIGRATIONS = (
    SQLiteMigration(
        version=4, name="retire_session_summary_data", apply=_retire_tables
    ),
)


def retire_session_summary_data(system_root: str | None = None) -> None:
    """Run only through the backed-up system migration coordinator."""
    path = Path(get_system_database_path(DB_NAME, system_root))
    if not path.exists():
        return
    conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        result = apply_sqlite_migrations(
            conn,
            namespace=MIGRATION_NAMESPACE,
            migrations=SESSION_SUMMARY_RETIREMENT_MIGRATIONS,
        )
    finally:
        conn.close()
    if result.applied:
        logger.info(
            "Legacy derived session summary tables retired",
            data={
                "event": "session_summary_data_retired",
                "status": "completed",
                "database_path": str(path),
                "retired_tables": list(_RETIRED_TABLES),
            },
        )
