"""Explicit historical SQLite schema and rows for startup migration."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


def create_legacy_database(db_path: Path, *, vault_name: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(UTC) - timedelta(days=1)
    expires_at = created_at + timedelta(days=30)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "\n                CREATE TABLE snapshot_sets (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,\n                    task_id VARCHAR NOT NULL,\n                    task_kind VARCHAR,\n                    task_source VARCHAR,\n                    task_scope VARCHAR,\n                    task_label VARCHAR,\n                    vault_id VARCHAR NOT NULL,\n                    vault_name VARCHAR NOT NULL,\n                    purpose VARCHAR NOT NULL,\n                    scope_kind VARCHAR,\n                    scope_id VARCHAR,\n                    snapshot_root VARCHAR NOT NULL,\n                    status VARCHAR NOT NULL,\n                    created_at DATETIME NOT NULL,\n                    expires_at DATETIME,\n                    rolled_back_at DATETIME\n                )\n                "
        )
        conn.execute(
            "\n                CREATE TABLE file_snapshots (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,\n                    snapshot_set_id INTEGER NOT NULL,\n                    task_id VARCHAR NOT NULL,\n                    vault_id VARCHAR NOT NULL,\n                    vault_name VARCHAR NOT NULL,\n                    path VARCHAR NOT NULL,\n                    source VARCHAR NOT NULL,\n                    file_exists BOOLEAN NOT NULL,\n                    content_hash VARCHAR,\n                    snapshot_ref VARCHAR,\n                    created_at DATETIME NOT NULL,\n                    expires_at DATETIME\n                )\n                "
        )
        conn.execute(
            "\n                INSERT INTO snapshot_sets (\n                    id, task_id, task_kind, task_source, task_scope, task_label,\n                    vault_id, vault_name, purpose, scope_kind, scope_id,\n                    snapshot_root, status, created_at, expires_at, rolled_back_at\n                )\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ",
            (
                700,
                "legacy-task",
                "workflow",
                "api",
                "workflow_vault:" + vault_name,
                vault_name + "/legacy",
                "legacy-vault-id",
                vault_name,
                "rollback",
                "task",
                "legacy-task",
                str(db_path.parent / "vault_snapshots" / "700"),
                "active",
                created_at.isoformat(),
                expires_at.isoformat(),
                None,
            ),
        )
        conn.execute(
            "\n                INSERT INTO file_snapshots (\n                    id, snapshot_set_id, task_id, vault_id, vault_name, path,\n                    source, file_exists, content_hash, snapshot_ref, created_at,\n                    expires_at\n                )\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ",
            (
                701,
                700,
                "legacy-task",
                "legacy-vault-id",
                vault_name,
                "notes/created.md",
                "task_mutation_before",
                0,
                None,
                None,
                created_at.isoformat(),
                expires_at.isoformat(),
            ),
        )
        conn.execute(
            "\n                CREATE TABLE vault_mutations (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,\n                    activity_id VARCHAR NOT NULL,\n                    operation_id VARCHAR NOT NULL,\n                    legacy_task_mutation_id INTEGER UNIQUE,\n                    path VARCHAR NOT NULL,\n                    related_path VARCHAR,\n                    target_kind VARCHAR NOT NULL,\n                    operation VARCHAR NOT NULL,\n                    status VARCHAR NOT NULL,\n                    event_sequence INTEGER,\n                    before_exists BOOLEAN NOT NULL,\n                    before_hash VARCHAR,\n                    before_snapshot_id INTEGER,\n                    after_exists BOOLEAN NOT NULL,\n                    after_hash VARCHAR,\n                    after_snapshot_id INTEGER,\n                    snapshot_ref VARCHAR,\n                    created_at DATETIME NOT NULL,\n                    expires_at DATETIME,\n                    metadata_json TEXT\n                )\n                "
        )
        conn.execute(
            "\n                CREATE TABLE task_file_mutations (\n                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,\n                    task_id VARCHAR NOT NULL,\n                    vault_id VARCHAR NOT NULL,\n                    vault_name VARCHAR NOT NULL,\n                    path VARCHAR NOT NULL,\n                    related_path VARCHAR,\n                    operation VARCHAR NOT NULL,\n                    before_exists BOOLEAN NOT NULL,\n                    before_hash VARCHAR,\n                    after_exists BOOLEAN NOT NULL,\n                    after_hash VARCHAR,\n                    snapshot_ref VARCHAR,\n                    created_at DATETIME NOT NULL\n                )\n                "
        )
        conn.executemany(
            "\n                INSERT INTO task_file_mutations (\n                    task_id, vault_id, vault_name, path, related_path, operation,\n                    before_exists, before_hash, after_exists, after_hash,\n                    snapshot_ref, created_at\n                )\n                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)\n                ",
            [
                (
                    "legacy-task",
                    "legacy-vault-id",
                    vault_name,
                    "notes/created.md",
                    None,
                    "write",
                    0,
                    None,
                    1,
                    "created-hash",
                    None,
                    created_at.isoformat(),
                ),
                (
                    "legacy-task",
                    "legacy-vault-id",
                    vault_name,
                    "notes/source.md",
                    "notes/destination.md",
                    "move",
                    1,
                    "move-hash",
                    0,
                    None,
                    "files/notes/source.md",
                    created_at.isoformat(),
                ),
                (
                    "legacy-task",
                    "legacy-vault-id",
                    vault_name,
                    "notes/destination.md",
                    "notes/source.md",
                    "move",
                    0,
                    None,
                    1,
                    "move-hash",
                    None,
                    created_at.isoformat(),
                ),
            ],
        )
        conn.execute("ALTER TABLE task_file_mutations ADD COLUMN expires_at DATETIME")
        conn.execute(
            "UPDATE task_file_mutations SET expires_at = ?", (expires_at.isoformat(),)
        )
