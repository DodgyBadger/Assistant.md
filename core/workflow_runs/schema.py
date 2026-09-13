"""Versioned schema for durable workflow run history."""

from __future__ import annotations

import sqlite3

from core.database import connect_sqlite_from_system_db
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations

DB_NAME = "workflow_runs"
MIGRATION_NAMESPACE = "workflow_runs"

WORKFLOW_RUN_MIGRATIONS = (
    SQLiteMigration(
        version=1,
        name="create_workflow_run_history",
        apply=lambda conn: _create_workflow_run_schema(conn),
    ),
    SQLiteMigration(
        version=2,
        name="index_cross_vault_workflow_history",
        apply=lambda conn: _create_workflow_name_index(conn),
    ),
    SQLiteMigration(
        version=3,
        name="add_workflow_run_owner_principal",
        apply=lambda conn: _migrate_workflow_run_owners(conn),
    ),
)


def ensure_workflow_run_schema(
    system_root: str | None = None,
    *,
    apply_migrations: bool = False,
) -> None:
    """Create workflow-run storage and optionally record release migrations."""
    conn = connect_sqlite_from_system_db(DB_NAME, system_root)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        if apply_migrations:
            apply_sqlite_migrations(
                conn,
                namespace=MIGRATION_NAMESPACE,
                migrations=WORKFLOW_RUN_MIGRATIONS,
            )
        else:
            _create_workflow_run_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _create_workflow_run_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            run_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            workflow_name TEXT NOT NULL,
            vault_name TEXT NOT NULL,
            source TEXT NOT NULL,
            owner_principal_id TEXT NOT NULL,
            task_id TEXT,
            step_name TEXT,
            scheduler_job_id TEXT,
            scheduler_event_key TEXT,
            scheduled_run_time TEXT,
            status TEXT NOT NULL,
            reason TEXT,
            message TEXT,
            failure_json TEXT,
            output_files_json TEXT NOT NULL DEFAULT '[]',
            execution_time_seconds REAL,
            queued_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_terminal
        ON workflow_runs(workflow_id, completed_at DESC, run_id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_vault_terminal
        ON workflow_runs(vault_name, completed_at DESC, run_id DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_source_terminal
        ON workflow_runs(source, completed_at DESC)
        """
    )
    _create_workflow_name_index(conn)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_scheduler_event
        ON workflow_runs(scheduler_event_key)
        WHERE scheduler_event_key IS NOT NULL
        """
    )
    _create_latest_runs_view(conn)


def _migrate_workflow_run_owners(conn: sqlite3.Connection) -> None:
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(workflow_runs)").fetchall()
    }
    if "owner_principal_id" not in columns:
        conn.execute(
            """
            ALTER TABLE workflow_runs
            ADD COLUMN owner_principal_id TEXT NOT NULL DEFAULT 'local-user'
            """
        )
    conn.execute(
        """
        UPDATE workflow_runs SET owner_principal_id = 'local-user'
        WHERE owner_principal_id IS NULL OR TRIM(owner_principal_id) = ''
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_owner_terminal
        ON workflow_runs(owner_principal_id, completed_at DESC)
        """
    )


def _create_workflow_name_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_workflow_runs_name_terminal
        ON workflow_runs(workflow_name, completed_at DESC, run_id DESC)
        """
    )


def _create_latest_runs_view(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW IF EXISTS workflow_latest_runs")
    conn.execute(
        """
        CREATE VIEW workflow_latest_runs AS
        SELECT runs.*
        FROM workflow_runs AS runs
        WHERE runs.completed_at IS NOT NULL
          AND runs.run_id = (
              SELECT candidate.run_id
              FROM workflow_runs AS candidate
              WHERE candidate.workflow_id = runs.workflow_id
                AND candidate.completed_at IS NOT NULL
              ORDER BY candidate.completed_at DESC, candidate.run_id DESC
              LIMIT 1
          )
        """
    )
