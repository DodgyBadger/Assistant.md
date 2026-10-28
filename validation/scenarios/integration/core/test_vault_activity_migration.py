"""Actual startup migration preserves legacy vault activity and snapshot ownership."""

from __future__ import annotations

import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.activity_migration import (
    create_legacy_database,
)

pytestmark = pytest.mark.scenario


async def test_startup_migrates_legacy_vault_activity_and_snapshots(
    app: Application,
) -> None:
    from core.migration_backups import MIGRATION_BACKUP_DIRECTORY

    vault = app.vault("VaultActivityMigrationVault")
    system_root = app.root / "system"
    db_path = system_root / "vault_state.db"
    create_legacy_database(db_path, vault_name=vault.name)
    from core.system_migrations import get_system_migration_status

    pending_status = get_system_migration_status(system_root)
    pending_target = next(
        target for target in pending_status.targets if target.db_name == "vault_state"
    )
    assert pending_target.pending_versions == (
        1,
    ), "Central migration status should expose the pending vault-state migration"
    await app.start()
    assert (
        len(
            list(
                (system_root / MIGRATION_BACKUP_DIRECTORY).glob(
                    "vault_state.db.backup-*"
                )
            )
        )
        == 1
    ), "Startup should back up the existing vault-state database before migration"
    from core.vault_state import VaultStateService

    service = VaultStateService()
    groups = service.list_activities(vault_name=vault.name, include_expired=True)
    assert len(groups) == 1, "Legacy task rows should become one activity"
    if groups:
        group = groups[0]
        assert group.task_id == "legacy-task", "Task provenance should be retained"
        assert group.status == "recorded", "Historical outcome should remain explicit"
        assert group.mutation_count == 3, "All legacy path rows should be retained"
        assert (
            group.operation_count == 2
        ), "Reciprocal move rows should count as one logical operation"
        move_operation_ids = {
            row.operation_id for row in group.mutations if row.operation == "move"
        }
        assert (
            len(move_operation_ids) == 1
        ), "Reciprocal legacy move rows should share an operation id"
    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert (
            "task_file_mutations" not in tables
        ), "Legacy mutation storage should be removed after verified backfill"
        assert (
            conn.execute("SELECT COUNT(*) FROM vault_activities").fetchone()[0] == 1
        ), "Repeated service initialization must not duplicate activities"
        assert (
            conn.execute("SELECT COUNT(*) FROM vault_mutations").fetchone()[0] == 3
        ), "Service initialization should read the centrally migrated ledger"
        mutation_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(vault_mutations)")
        }
        assert (
            "legacy_task_mutation_id" not in mutation_columns
        ), "Temporary migration provenance should be removed from the current schema"
        assert conn.execute(
            "\n                    SELECT version\n                    FROM schema_migrations\n                    WHERE namespace = 'vault_state'\n                    ORDER BY version\n                    "
        ).fetchall() == [
            (1,)
        ], "Vault-state migration should be recorded in the shared version ledger"
        snapshot_columns = {
            str(row[1]): int(row[3])
            for row in conn.execute("PRAGMA table_info(snapshot_sets)")
        }
        assert (
            "activity_id" in snapshot_columns
        ), "Snapshot sets should support activity ownership"
        assert (
            snapshot_columns.get("task_id") == 0
        ), "Snapshot set task ownership should be optional"
        assert conn.execute(
            "SELECT id, activity_id, task_id FROM snapshot_sets WHERE id = 700"
        ).fetchone() == (
            700,
            None,
            "legacy-task",
        ), "Existing task snapshot ownership should survive migration"
        assert conn.execute(
            "SELECT id, activity_id, task_id FROM file_snapshots WHERE id = 701"
        ).fetchone() == (
            701,
            None,
            "legacy-task",
        ), "Existing file snapshot ids and task provenance should survive migration"
    applied_status = get_system_migration_status(system_root)
    applied_target = next(
        target for target in applied_status.targets if target.db_name == "vault_state"
    )
    assert (
        applied_target.pending_versions == ()
    ), "Central migration status should report vault state as current"
