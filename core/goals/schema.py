"""SQLite schema helpers for goal_ops."""

from __future__ import annotations

import sqlite3

from core.database import connect_sqlite_from_system_db
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations

DB_NAME = "goal_ops"
MIGRATION_NAMESPACE = "goal_ops"

GOAL_OPS_MIGRATIONS = (
    SQLiteMigration(
        version=1,
        name="create_goal_ops_tables",
        apply=lambda conn: _create_goal_ops_tables(conn),
    ),
    SQLiteMigration(
        version=2,
        name="add_goal_source_provenance",
        apply=lambda conn: _add_goal_source_provenance(conn),
    ),
    SQLiteMigration(
        version=3,
        name="add_goal_plan_snapshot",
        apply=lambda conn: _add_goal_plan_snapshot(conn),
    ),
)


def ensure_goal_ops_schema(
    system_root: str | None = None,
    *,
    apply_migrations: bool = False,
) -> None:
    """Create goal_ops tables when they do not already exist."""
    conn = connect_sqlite_from_system_db(DB_NAME, system_root)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if apply_migrations:
            apply_sqlite_migrations(
                conn, namespace=MIGRATION_NAMESPACE, migrations=GOAL_OPS_MIGRATIONS
            )
            conn.commit()
        else:
            _create_goal_ops_tables(conn)
            conn.commit()
    finally:
        conn.close()


def _create_goal_ops_tables(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            goal_id TEXT PRIMARY KEY,
            vault_name TEXT NOT NULL,
            workspace_path_hint TEXT,
            source_type TEXT,
            source_id TEXT,
            source_task_id TEXT,
            source_label TEXT,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_json TEXT,
            success_criteria_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_goals_vault_status_updated
        ON goals(vault_name, status, updated_at)
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_goals_vault_workspace
        ON goals(vault_name, workspace_path_hint)
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_goals_vault_source
        ON goals(vault_name, source_type, source_id)
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goal_steps (
            step_id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (goal_id)
                REFERENCES goals(goal_id)
                ON DELETE CASCADE
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_goal_steps_goal_position
        ON goal_steps(goal_id, position, created_at)
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_goal_steps_goal_status
        ON goal_steps(goal_id, status)
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goal_events (
            event_id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            step_id TEXT,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (goal_id)
                REFERENCES goals(goal_id)
                ON DELETE CASCADE,
            FOREIGN KEY (step_id)
                REFERENCES goal_steps(step_id)
                ON DELETE SET NULL
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_goal_events_goal_created
        ON goal_events(goal_id, created_at)
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goal_checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            goal_id TEXT NOT NULL,
            step_id TEXT,
            summary TEXT NOT NULL,
            current_state TEXT,
            next_actions_json TEXT NOT NULL DEFAULT '[]',
            open_questions_json TEXT NOT NULL DEFAULT '[]',
            risks_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (goal_id)
                REFERENCES goals(goal_id)
                ON DELETE CASCADE,
            FOREIGN KEY (step_id)
                REFERENCES goal_steps(step_id)
                ON DELETE SET NULL
        )
        """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_goal_checkpoints_goal_created
        ON goal_checkpoints(goal_id, created_at)
        """)


def _add_goal_source_provenance(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "goals")
    for column in ("source_type", "source_id", "source_task_id", "source_label"):
        if column not in columns:
            conn.execute(f"ALTER TABLE goals ADD COLUMN {column} TEXT")
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_goals_vault_source
        ON goals(vault_name, source_type, source_id)
        """)


def _add_goal_plan_snapshot(conn: sqlite3.Connection) -> None:
    columns = _table_columns(conn, "goals")
    if "plan_json" not in columns:
        conn.execute("ALTER TABLE goals ADD COLUMN plan_json TEXT")


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row[1]) for row in rows}
