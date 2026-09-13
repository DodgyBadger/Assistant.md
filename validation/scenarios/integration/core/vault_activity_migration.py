"""Integration scenario for the legacy vault-mutation ledger migration."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-vault-activity-migration-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.migration_backups import MIGRATION_BACKUP_DIRECTORY  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class VaultActivityMigrationScenario(BaseScenario):
    """Validate legacy task mutations become durable generalized activity."""

    async def test_scenario(self):
        vault = self.create_vault("VaultActivityMigrationVault")
        system_root = self._get_system_controller()._system_root
        db_path = system_root / "vault_state.db"
        self._create_legacy_database(db_path, vault_name=vault.name)

        from core.system_migrations import get_system_migration_status

        pending_status = get_system_migration_status(system_root)
        pending_target = next(
            target
            for target in pending_status.targets
            if target.db_name == "vault_state"
        )
        self.soft_assert_equal(
            pending_target.pending_versions,
            (1,),
            "Central migration status should expose the pending vault-state migration",
        )

        await self.start_system()

        self.soft_assert_equal(
            len(
                list(
                    (system_root / MIGRATION_BACKUP_DIRECTORY).glob(
                        "vault_state.db.backup-*"
                    )
                )
            ),
            1,
            "Startup should back up the existing vault-state database before migration",
        )

        from core.vault_state import VaultStateService

        service = VaultStateService()
        groups = service.list_activities(vault_name=vault.name, include_expired=True)
        self.soft_assert_equal(
            len(groups), 1, "Legacy task rows should become one activity"
        )
        if groups:
            group = groups[0]
            self.soft_assert_equal(
                group.task_id, "legacy-task", "Task provenance should be retained"
            )
            self.soft_assert_equal(
                group.status, "recorded", "Historical outcome should remain explicit"
            )
            self.soft_assert_equal(
                group.mutation_count, 3, "All legacy path rows should be retained"
            )
            self.soft_assert_equal(
                group.operation_count,
                2,
                "Reciprocal move rows should count as one logical operation",
            )
            move_operation_ids = {
                row.operation_id for row in group.mutations if row.operation == "move"
            }
            self.soft_assert_equal(
                len(move_operation_ids),
                1,
                "Reciprocal legacy move rows should share an operation id",
            )

        with sqlite3.connect(db_path) as conn:
            tables = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.soft_assert(
                "task_file_mutations" not in tables,
                "Legacy mutation storage should be removed after verified backfill",
            )
            self.soft_assert_equal(
                conn.execute("SELECT COUNT(*) FROM vault_activities").fetchone()[0],
                1,
                "Repeated service initialization must not duplicate activities",
            )
            self.soft_assert_equal(
                conn.execute("SELECT COUNT(*) FROM vault_mutations").fetchone()[0],
                3,
                "Service initialization should read the centrally migrated ledger",
            )
            mutation_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(vault_mutations)")
            }
            self.soft_assert(
                "legacy_task_mutation_id" not in mutation_columns,
                "Temporary migration provenance should be removed from the current schema",
            )
            self.soft_assert_equal(
                conn.execute(
                    """
                    SELECT version
                    FROM schema_migrations
                    WHERE namespace = 'vault_state'
                    ORDER BY version
                    """
                ).fetchall(),
                [(1,)],
                "Vault-state migration should be recorded in the shared version ledger",
            )
            snapshot_columns = {
                str(row[1]): int(row[3])
                for row in conn.execute("PRAGMA table_info(snapshot_sets)")
            }
            self.soft_assert(
                "activity_id" in snapshot_columns,
                "Snapshot sets should support activity ownership",
            )
            self.soft_assert_equal(
                snapshot_columns.get("task_id"),
                0,
                "Snapshot set task ownership should be optional",
            )
            self.soft_assert_equal(
                conn.execute(
                    "SELECT id, activity_id, task_id FROM snapshot_sets WHERE id = 700"
                ).fetchone(),
                (700, None, "legacy-task"),
                "Existing task snapshot ownership should survive migration",
            )
            self.soft_assert_equal(
                conn.execute(
                    "SELECT id, activity_id, task_id FROM file_snapshots WHERE id = 701"
                ).fetchone(),
                (701, None, "legacy-task"),
                "Existing file snapshot ids and task provenance should survive migration",
            )

        applied_status = get_system_migration_status(system_root)
        applied_target = next(
            target
            for target in applied_status.targets
            if target.db_name == "vault_state"
        )
        self.soft_assert_equal(
            applied_target.pending_versions,
            (),
            "Central migration status should report vault state as current",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    @staticmethod
    def _create_legacy_database(db_path: Path, *, vault_name: str) -> None:
        created_at = datetime.now(UTC) - timedelta(days=1)
        expires_at = created_at + timedelta(days=30)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE snapshot_sets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    task_id VARCHAR NOT NULL,
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
                """
            )
            conn.execute(
                """
                CREATE TABLE file_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    snapshot_set_id INTEGER NOT NULL,
                    task_id VARCHAR NOT NULL,
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
                """
            )
            conn.execute(
                """
                INSERT INTO snapshot_sets (
                    id, task_id, task_kind, task_source, task_scope, task_label,
                    vault_id, vault_name, purpose, scope_kind, scope_id,
                    snapshot_root, status, created_at, expires_at, rolled_back_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
                """
                INSERT INTO file_snapshots (
                    id, snapshot_set_id, task_id, vault_id, vault_name, path,
                    source, file_exists, content_hash, snapshot_ref, created_at,
                    expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
                """
                CREATE TABLE vault_mutations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    activity_id VARCHAR NOT NULL,
                    operation_id VARCHAR NOT NULL,
                    legacy_task_mutation_id INTEGER UNIQUE,
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
                """
            )
            conn.execute(
                """
                CREATE TABLE task_file_mutations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                    task_id VARCHAR NOT NULL,
                    vault_id VARCHAR NOT NULL,
                    vault_name VARCHAR NOT NULL,
                    path VARCHAR NOT NULL,
                    related_path VARCHAR,
                    operation VARCHAR NOT NULL,
                    before_exists BOOLEAN NOT NULL,
                    before_hash VARCHAR,
                    after_exists BOOLEAN NOT NULL,
                    after_hash VARCHAR,
                    snapshot_ref VARCHAR,
                    created_at DATETIME NOT NULL
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO task_file_mutations (
                    task_id, vault_id, vault_name, path, related_path, operation,
                    before_exists, before_hash, after_exists, after_hash,
                    snapshot_ref, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
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
            conn.execute(
                "ALTER TABLE task_file_mutations ADD COLUMN expires_at DATETIME"
            )
            conn.execute(
                "UPDATE task_file_mutations SET expires_at = ?",
                (expires_at.isoformat(),),
            )


if __name__ == "__main__":
    asyncio.run(VaultActivityMigrationScenario().test_scenario())
