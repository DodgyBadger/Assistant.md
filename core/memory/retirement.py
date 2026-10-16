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


class SessionSummaryRetirementBlocked(RuntimeError):
    """Unknown dependent schema must be resolved before owned tables are dropped."""

    def __init__(self, dependencies: tuple[tuple[str, str], ...]) -> None:
        self.dependencies = dependencies
        related = ", ".join(f"{source} -> {target}" for source, target in dependencies)
        super().__init__(
            f"Legacy summary retirement blocked by custom foreign keys: {related}. Preserve or migrate these dependencies before restarting."
        )


def _retire_tables(conn: sqlite3.Connection) -> None:
    # DROP statements must share the runner's bookkeeping transaction.
    if not conn.in_transaction:
        conn.execute("BEGIN")
    table_types = {
        str(row[1]).casefold(): str(row[2])
        for row in conn.execute("PRAGMA main.table_list")
    }
    retired = {table.casefold() for table in _RETIRED_TABLES} & table_types.keys()
    retired_virtual = {table for table in retired if table_types[table] == "virtual"}
    # Dropping a virtual FTS table also drops its SQLite-owned shadow tables.
    retired |= {
        table
        for table, kind in table_types.items()
        if kind == "shadow"
        and any(table.startswith(f"{parent}_") for parent in retired_virtual)
    }
    dependencies: set[tuple[str, str]] = set()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    for (table,) in tables:
        if table.casefold() in retired:
            continue
        for (target,) in conn.execute(
            'SELECT "table" FROM pragma_foreign_key_list(?)', (table,)
        ):
            if target.casefold() in retired:
                dependencies.add((table, target))
    if dependencies:
        raise SessionSummaryRetirementBlocked(tuple(sorted(dependencies)))
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
    except SessionSummaryRetirementBlocked as exc:
        logger.error(
            "Legacy summary retirement blocked by custom schema dependencies",
            data={
                "event": "session_summary_data_retirement_blocked",
                "status": "blocked",
                "issue": "session-summary-retirement:custom-foreign-keys",
                "database_path": str(path),
                "backup_directory": str(path.parent / "migration_backups"),
                "dependencies": [list(item) for item in exc.dependencies],
                "error_type": type(exc).__name__,
                "reason": "custom_foreign_key_dependency",
                "error": "Custom foreign keys reference retired tables; migrate dependent schema before restarting.",
            },
        )
        raise
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
