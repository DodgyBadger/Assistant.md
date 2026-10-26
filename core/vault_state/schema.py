"""Versioned schema management for the vault-state database."""

from __future__ import annotations

import sqlite3
import threading
from typing import cast

from sqlalchemy import Table

from core.database import (
    connect_sqlite_from_system_db,
    create_engine_from_system_db,
    create_tables,
)
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations
from core.vault_state.models import (
    FileSnapshot,
    SnapshotSet,
    VaultActivity,
    VaultFile,
    VaultFileEvent,
    VaultMutation,
    VaultRecord,
)

DB_NAME = "vault_state"
MIGRATION_NAMESPACE = "vault_state"

VAULT_STATE_MIGRATIONS = (
    SQLiteMigration(
        version=1,
        name="generalize_activity_and_snapshot_ownership",
        apply=lambda conn: _migrate_activity_and_snapshot_ownership(conn),
    ),
)
_SCHEMA_LOCK = threading.RLock()


def ensure_vault_state_schema(
    system_root: str | None = None,
    *,
    apply_migrations: bool = False,
) -> None:
    """Create current vault-state tables and optionally apply migrations."""
    with _SCHEMA_LOCK:
        engine = create_engine_from_system_db(DB_NAME, system_root)
        try:
            create_tables(
                engine,
                *(
                    cast(Table, model.__table__)
                    for model in (
                        VaultRecord,
                        VaultFile,
                        VaultFileEvent,
                        VaultActivity,
                        VaultMutation,
                        SnapshotSet,
                        FileSnapshot,
                    )
                ),
            )
        finally:
            engine.dispose()

        if not apply_migrations:
            return
        conn = connect_sqlite_from_system_db(DB_NAME, system_root)
        try:
            apply_sqlite_migrations(
                conn,
                namespace=MIGRATION_NAMESPACE,
                migrations=VAULT_STATE_MIGRATIONS,
            )
            conn.commit()
        finally:
            conn.close()


def _migrate_activity_and_snapshot_ownership(conn: sqlite3.Connection) -> None:
    _migrate_snapshot_ownership(conn)
    _remove_legacy_mutation_provenance(conn)
    _migrate_task_mutations(conn)
    _ensure_current_indexes(conn)


def _migrate_snapshot_ownership(conn: sqlite3.Connection) -> None:
    snapshot_columns = _table_columns(conn, "snapshot_sets")
    file_snapshot_columns = _table_columns(conn, "file_snapshots")
    rebuild_snapshot_sets = (
        "activity_id" not in snapshot_columns
        or snapshot_columns.get("task_id", {}).get("notnull") == 1
    )
    rebuild_file_snapshots = (
        "activity_id" not in file_snapshot_columns
        or file_snapshot_columns.get("task_id", {}).get("notnull") == 1
    )
    if rebuild_snapshot_sets:
        rebuild_file_snapshots = True
    if not rebuild_snapshot_sets and not rebuild_file_snapshots:
        return

    if rebuild_file_snapshots:
        conn.execute("ALTER TABLE file_snapshots RENAME TO file_snapshots_task_owned")
    if rebuild_snapshot_sets:
        conn.execute("ALTER TABLE snapshot_sets RENAME TO snapshot_sets_task_owned")
        _create_snapshot_sets(conn)
        conn.execute("""
            INSERT INTO snapshot_sets (
                id, activity_id, task_id, task_kind, task_source, task_scope,
                task_label, vault_id, vault_name, purpose, scope_kind, scope_id,
                snapshot_root, status, created_at, expires_at, rolled_back_at
            )
            SELECT
                id, NULL, task_id, task_kind, task_source, task_scope,
                task_label, vault_id, vault_name, purpose, scope_kind, scope_id,
                snapshot_root, status, created_at, expires_at, rolled_back_at
            FROM snapshot_sets_task_owned
            """)
    if rebuild_file_snapshots:
        _create_file_snapshots(conn)
        conn.execute("""
            INSERT INTO file_snapshots (
                id, snapshot_set_id, activity_id, task_id, vault_id, vault_name,
                path, source, file_exists, content_hash, snapshot_ref, created_at,
                expires_at
            )
            SELECT
                id, snapshot_set_id, NULL, task_id, vault_id, vault_name,
                path, source, file_exists, content_hash, snapshot_ref, created_at,
                expires_at
            FROM file_snapshots_task_owned
            """)
        conn.execute("DROP TABLE file_snapshots_task_owned")
    if rebuild_snapshot_sets:
        conn.execute("DROP TABLE snapshot_sets_task_owned")


def _migrate_task_mutations(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "task_file_mutations"):
        return
    _ensure_legacy_mutation_columns(conn)
    rolled_back_tasks = {str(row[0]) for row in conn.execute("""
            SELECT DISTINCT task_id
            FROM snapshot_sets
            WHERE purpose = 'rollback'
              AND status = 'rolled_back'
              AND task_id IS NOT NULL
            """)}
    rows = conn.execute("""
        SELECT
            id, task_id, task_kind, task_source, task_scope, task_label,
            goal_id, step_id, vault_id, vault_name, path, related_path,
            operation, event_sequence, before_exists, before_hash,
            before_snapshot_id, after_exists, after_hash, after_snapshot_id,
            snapshot_ref, created_at, expires_at
        FROM task_file_mutations
        ORDER BY id
        """).fetchall()
    activity_rows: dict[str, tuple[object, ...]] = {}
    activity_bounds: dict[str, tuple[str, str, object | None]] = {}
    for row in rows:
        activity_id = _task_activity_id(str(row[1]), str(row[8]))
        existing = activity_rows.get(activity_id)
        if existing is None:
            activity_rows[activity_id] = row
            timestamp = str(row[21])
            activity_bounds[activity_id] = (timestamp, timestamp, row[22])
            continue
        first_at, last_at, expires_at = activity_bounds[activity_id]
        timestamp = str(row[21])
        if row[22] is not None and (
            expires_at is None or str(row[22]) > str(expires_at)
        ):
            expires_at = row[22]
        activity_bounds[activity_id] = (
            min(first_at, timestamp),
            max(last_at, timestamp),
            expires_at,
        )

    for activity_id, row in activity_rows.items():
        task_id = str(row[1])
        status = "rolled_back" if task_id in rolled_back_tasks else "recorded"
        first_at, last_at, expires_at = activity_bounds[activity_id]
        conn.execute(
            """
            INSERT OR IGNORE INTO vault_activities (
                activity_id, vault_id, vault_name, kind, source, scope, label,
                task_id, goal_id, step_id, status, rollback_status, created_at,
                updated_at, completed_at, expires_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                activity_id,
                row[8],
                row[9],
                row[2] or "task",
                row[3] or "system",
                row[4],
                row[5] or task_id,
                task_id,
                row[6],
                row[7],
                status,
                "completed" if status == "rolled_back" else None,
                first_at,
                last_at,
                last_at,
                expires_at,
                '{"migrated": true}',
            ),
        )

    for row in rows:
        activity_id = _task_activity_id(str(row[1]), str(row[8]))
        conn.execute(
            """
            INSERT OR IGNORE INTO vault_mutations (
                id, activity_id, operation_id, path, related_path, target_kind,
                operation, status, event_sequence, before_exists, before_hash,
                before_snapshot_id, after_exists, after_hash, after_snapshot_id,
                snapshot_ref, created_at, expires_at, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, 'file', ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row[0],
                activity_id,
                _legacy_operation_id(row),
                row[10],
                row[11],
                row[12],
                row[13],
                row[14],
                row[15],
                row[16],
                row[17],
                row[18],
                row[19],
                row[20],
                row[21],
                row[22],
                '{"migrated": true}',
            ),
        )
    conn.execute("DROP TABLE task_file_mutations")


def _remove_legacy_mutation_provenance(conn: sqlite3.Connection) -> None:
    if "legacy_task_mutation_id" not in _table_columns(conn, "vault_mutations"):
        return
    conn.execute("ALTER TABLE vault_mutations RENAME TO vault_mutations_with_legacy_id")
    conn.execute("""
        CREATE TABLE vault_mutations (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            activity_id VARCHAR NOT NULL,
            operation_id VARCHAR NOT NULL,
            path VARCHAR NOT NULL,
            related_path VARCHAR,
            target_kind VARCHAR NOT NULL,
            operation VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            event_sequence INTEGER,
            before_exists BOOLEAN NOT NULL,
            before_hash VARCHAR,
            before_snapshot_id INTEGER,
            after_exists BOOLEAN NOT NULL,
            after_hash VARCHAR,
            after_snapshot_id INTEGER,
            snapshot_ref VARCHAR,
            created_at DATETIME NOT NULL,
            expires_at DATETIME,
            metadata_json TEXT
        )
        """)
    conn.execute("""
        INSERT INTO vault_mutations (
            id, activity_id, operation_id, path, related_path, target_kind,
            operation, status, event_sequence, before_exists, before_hash,
            before_snapshot_id, after_exists, after_hash, after_snapshot_id,
            snapshot_ref, created_at, expires_at, metadata_json
        )
        SELECT
            id, activity_id, operation_id, path, related_path, target_kind,
            operation, status, event_sequence, before_exists, before_hash,
            before_snapshot_id, after_exists, after_hash, after_snapshot_id,
            snapshot_ref, created_at, expires_at, metadata_json
        FROM vault_mutations_with_legacy_id
        """)
    conn.execute("DROP TABLE vault_mutations_with_legacy_id")


def _ensure_legacy_mutation_columns(conn: sqlite3.Connection) -> None:
    existing = _table_columns(conn, "task_file_mutations")
    desired = {
        "task_kind": "VARCHAR",
        "task_source": "VARCHAR",
        "task_scope": "VARCHAR",
        "task_label": "VARCHAR",
        "goal_id": "VARCHAR",
        "step_id": "VARCHAR",
        "related_path": "VARCHAR",
        "event_sequence": "INTEGER",
        "before_snapshot_id": "INTEGER",
        "after_snapshot_id": "INTEGER",
        "expires_at": "DATETIME",
    }
    for name, column_type in desired.items():
        if name not in existing:
            conn.execute(
                f"ALTER TABLE task_file_mutations ADD COLUMN {name} {column_type}"
            )


def _create_snapshot_sets(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE snapshot_sets (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            activity_id VARCHAR,
            task_id VARCHAR,
            task_kind VARCHAR,
            task_source VARCHAR,
            task_scope VARCHAR,
            task_label VARCHAR,
            vault_id VARCHAR NOT NULL,
            vault_name VARCHAR NOT NULL,
            purpose VARCHAR NOT NULL,
            scope_kind VARCHAR,
            scope_id VARCHAR,
            snapshot_root VARCHAR NOT NULL,
            status VARCHAR NOT NULL,
            created_at DATETIME NOT NULL,
            expires_at DATETIME,
            rolled_back_at DATETIME
        )
        """)


def _create_file_snapshots(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE file_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            snapshot_set_id INTEGER NOT NULL,
            activity_id VARCHAR,
            task_id VARCHAR,
            vault_id VARCHAR NOT NULL,
            vault_name VARCHAR NOT NULL,
            path VARCHAR NOT NULL,
            source VARCHAR NOT NULL,
            file_exists BOOLEAN NOT NULL,
            content_hash VARCHAR,
            snapshot_ref VARCHAR,
            created_at DATETIME NOT NULL,
            expires_at DATETIME
        )
        """)


def _ensure_current_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_vault_mutations_activity_id ON vault_mutations (activity_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_vault_mutations_operation_id ON vault_mutations (operation_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_snapshot_sets_activity_id ON snapshot_sets (activity_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_file_snapshots_activity_id ON file_snapshots (activity_id)"
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _table_columns(
    conn: sqlite3.Connection,
    table_name: str,
) -> dict[str, dict[str, object]]:
    if not _table_exists(conn, table_name):
        return {}
    return {
        str(row[1]): {"type": row[2], "notnull": int(row[3])}
        for row in conn.execute(f"PRAGMA table_info({table_name})")
    }


def _task_activity_id(task_id: str, vault_id: str) -> str:
    return f"task:{task_id}:{vault_id}"


def _legacy_operation_id(row: tuple[object, ...]) -> str:
    if row[12] == "move" and row[11]:
        first, second = sorted((str(row[10]), str(row[11])))
        return f"legacy-move:{row[1]}:{first}:{second}"
    return f"legacy:{row[0]}"
