"""Ordered/idempotent SQLite migrations and registered release migration artifacts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from core.chat import ChatStore
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations
from core.ingestion.service import IngestionService
from core.migration_backups import MIGRATION_BACKUP_DIRECTORY
from core.system_migrations import get_system_migration_status, run_system_migrations
from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.security_migrations import (
    activity_data,
    assert_operation_pairs,
    create_legacy_chat_sessions_db,
    create_legacy_ingestion_jobs_db,
    create_pre_retirement_chat_sessions_db,
    create_probe_table,
    insert_probe_row,
    migration_bootstrap_roots,
    migration_versions,
    table_exists,
)

pytestmark = pytest.mark.scenario


async def test_sqlite_migration_order_validation_and_rollback(app: Application) -> None:
    app.vault("SQLiteMigrations")
    await app.start()
    db_path = app.root / "sqlite_migrations.db"
    conn = sqlite3.connect(db_path)
    try:
        migrations = (
            SQLiteMigration(
                version=2,
                name="insert_probe_row",
                apply=insert_probe_row,
            ),
            SQLiteMigration(
                version=1,
                name="create_probe_table",
                apply=create_probe_table,
            ),
        )
        first = apply_sqlite_migrations(
            conn, namespace="validation", migrations=migrations
        )
        assert first.applied == (1, 2), "Migrations should apply in version order"
        assert first.skipped == (), "First migration run should not skip versions"
        values = [row[0] for row in conn.execute("SELECT value FROM probe").fetchall()]
        assert values == ["applied"], "Applied migration should mutate the target DB"
        second = apply_sqlite_migrations(
            conn, namespace="validation", migrations=migrations
        )
        assert second.applied == (), "Second migration run should not reapply versions"
        assert second.skipped == (
            1,
            2,
        ), "Second migration run should skip applied versions"
        try:
            apply_sqlite_migrations(
                conn,
                namespace="validation",
                migrations=(
                    SQLiteMigration(
                        version=1, name="one", apply=lambda connection: None
                    ),
                    SQLiteMigration(
                        version=1, name="duplicate", apply=lambda connection: None
                    ),
                ),
            )
        except ValueError as exc:
            assert "duplicate migration version" in str(
                exc
            ), "Duplicate migration versions should be rejected"
        else:
            raise AssertionError("Duplicate migration versions should fail")

        def fail_after_insert(connection: sqlite3.Connection) -> None:
            connection.execute("INSERT INTO probe (value) VALUES ('rolled_back')")
            raise RuntimeError("intentional migration failure")

        try:
            apply_sqlite_migrations(
                conn,
                namespace="validation",
                migrations=(
                    *migrations,
                    SQLiteMigration(
                        version=3, name="failing_migration", apply=fail_after_insert
                    ),
                ),
            )
        except RuntimeError as exc:
            assert (
                str(exc) == "intentional migration failure"
            ), "Failure should propagate"
        else:
            raise AssertionError("Failing migration should raise")
        values_after_failure = [
            row[0]
            for row in conn.execute("SELECT value FROM probe ORDER BY rowid").fetchall()
        ]
        assert values_after_failure == [
            "applied"
        ], "Failed migration should roll back its target-table changes"
        recorded_versions = [row[0] for row in conn.execute("""
                    SELECT version
                    FROM schema_migrations
                    WHERE namespace = 'validation'
                    ORDER BY version
                    """).fetchall()]
        assert recorded_versions == [1, 2], "Failed migration should not be recorded"
    finally:
        conn.close()


async def test_registered_system_migration_backups_and_retirement(
    app: Application,
) -> None:
    app.vault("SystemDatabaseMigrations")
    with migration_bootstrap_roots(
        app.root / "migration-data", app.root / "migration-system"
    ):
        system_root = app.root / "migration-system"
        system_root.mkdir(parents=True, exist_ok=True)
        chat_db = system_root / "chat_sessions.db"
        create_legacy_chat_sessions_db(chat_db)
        ingestion_db = system_root / "ingestion_jobs.db"
        create_legacy_ingestion_jobs_db(ingestion_db)
        legacy_backup = system_root / "vault_state.db.backup-legacy"
        legacy_backup.write_bytes(b"legacy migration backup")
        backup_directory = system_root / MIGRATION_BACKUP_DIRECTORY
        backup_directory.mkdir()
        existing_legacy_backup = backup_directory / legacy_backup.name
        existing_legacy_backup.write_bytes(b"existing migration backup")
        second_existing_legacy_backup = backup_directory / f"{legacy_backup.name} (2)"
        second_existing_legacy_backup.write_bytes(b"second existing migration backup")
        pending_before_store_initialization = get_system_migration_status(system_root)
        ChatStore(str(system_root))
        with migration_bootstrap_roots(app.root / "migration-data", system_root):
            IngestionService()
        with sqlite3.connect(ingestion_db) as conn:
            columns_before_migration = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(ingestion_jobs)")
            }
        assert (
            "selected_strategy" not in columns_before_migration
        ), "Ingestion service initialization must not bypass managed migrations"
        before = get_system_migration_status(system_root)
        assert (
            before.pending_count == pending_before_store_initialization.pending_count
        ), "Store initialization should not apply registered release migrations"
        after = run_system_migrations(system_root, backup=True)
        assert after.pending_count == 0, "Registered migrations should be applied"
        assert (
            not legacy_backup.exists()
        ), "A migration run should remove managed legacy backups from the system root"
        assert (
            existing_legacy_backup.read_bytes() == b"existing migration backup"
        ), "A migration run should not overwrite an existing organized backup"
        assert (
            second_existing_legacy_backup.read_bytes()
            == b"second existing migration backup"
        ), "A migration run should preserve every existing numbered backup"
        assert (
            backup_directory / f"{legacy_backup.name} (3)"
        ).read_bytes() == b"legacy migration backup", (
            "A migration run should choose the next free backup version"
        )
        target_by_db = {target.db_name: target for target in after.targets}
        chat_target = target_by_db["chat_sessions"]
        summary_target = target_by_db["session_summaries"]
        goal_target = target_by_db["goal_ops"]
        vault_target = target_by_db["vault_state"]
        workflow_runs_target = target_by_db["workflow_runs"]
        ingestion_target = target_by_db["ingestion_jobs"]
        assert (
            chat_target.backup_path is not None
        ), "Existing chat DB should be backed up"
        assert (
            summary_target.backup_path is None
        ), "Absent retired summary DB should not create a backup"
        assert not summary_target.exists and (
            not summary_target.pending_versions
        ), "Fresh installations must not create a retired summary database"
        assert (
            goal_target.backup_path is None
        ), "New goal_ops DB should not create an empty backup"
        assert (
            vault_target.backup_path is None
        ), "New vault-state DB should not create an empty backup"
        assert (
            workflow_runs_target.backup_path is None
        ), "New workflow-runs DB should not create an empty backup"
        assert (
            ingestion_target.backup_path is not None
        ), "Existing ingestion jobs DB should be backed up"
        if chat_target.backup_path:
            assert Path(chat_target.backup_path).exists(), "Chat DB backup should exist"
            assert (
                Path(chat_target.backup_path).parent == backup_directory
            ), "New database backups should be isolated from live system databases"
        with sqlite3.connect(chat_db) as conn:
            assert table_exists(
                conn, "chat_compaction_checkpoints"
            ), "Chat checkpoint table should exist after migration"
            assert migration_versions(conn, "chat_sessions") == [
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
                10,
            ], "Chat migration version should be recorded"
            checkpoint_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(chat_compaction_checkpoints)"
                )
            }
            assert (
                "replacement_source_sequence_indexes_json" in checkpoint_columns
            ), "Chat checkpoint migration should add replacement origins"
            assert not table_exists(
                conn, "chat_session_map_revisions"
            ), "Retired session-map revision storage should be absent after migration"
            assert not table_exists(
                conn, "chat_session_map_maintenance"
            ), "Retired session-map maintenance storage should be absent after migration"
            assert not table_exists(
                conn, "chat_session_map_attempts"
            ), "Retired session-map attempt storage should be absent after migration"
            owner = conn.execute(
                "SELECT owner_principal_id FROM chat_sessions WHERE session_id = 'legacy'"
            ).fetchone()
            assert (
                owner[0] if owner else None
            ) == "local-user", (
                "Legacy chat sessions should be assigned to the local user"
            )
        with sqlite3.connect(system_root / "goal_ops.db") as conn:
            assert table_exists(
                conn, "goals"
            ), "goal_ops goals table should exist after migration"
            assert migration_versions(conn, "goal_ops") == [
                1,
                2,
                3,
            ], "goal_ops migration version should be recorded"
        with sqlite3.connect(system_root / "vault_state.db") as conn:
            assert migration_versions(conn, "vault_state") == [
                1
            ], "Vault-state migration version should be recorded"
        with sqlite3.connect(system_root / "workflow_runs.db") as conn:
            assert table_exists(
                conn, "workflow_runs"
            ), "Workflow run history table should exist after migration"
            assert migration_versions(conn, "workflow_runs") == [
                1,
                2,
                3,
            ], "Workflow run migration versions should be recorded"
        with sqlite3.connect(ingestion_db) as conn:
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(ingestion_jobs)")
            }
            assert {
                "selected_strategy",
                "selected_provider",
                "selected_model",
                "strategy_attempts",
                "fallback_reason",
            }.issubset(columns), "Ingestion provenance columns should be added"
            assert migration_versions(conn, "ingestion_jobs") == [
                1
            ], "Ingestion migration version should be recorded"
        second = run_system_migrations(system_root, backup=True)
        assert second.pending_count == 0, "Second run should remain fully applied"
        retired_map_root = app.root / "retired-map-system"
        retired_map_root.mkdir()
        retired_map_db = retired_map_root / "chat_sessions.db"
        create_pre_retirement_chat_sessions_db(retired_map_db)
        retired_map_migrations = run_system_migrations(retired_map_root, backup=True)
        retired_map_chat_target = next(
            target
            for target in retired_map_migrations.targets
            if target.db_name == "chat_sessions"
        )
        assert (
            retired_map_chat_target.backup_path is not None
        ), "A pre-retirement chat database should be backed up before map teardown"
        if retired_map_chat_target.backup_path is not None:
            backup_path = Path(retired_map_chat_target.backup_path)
            assert backup_path.exists(), "The pre-retirement backup should exist"
            with sqlite3.connect(backup_path) as backup_conn:
                for table_name in (
                    "chat_session_map_revisions",
                    "chat_session_map_maintenance",
                    "chat_session_map_attempts",
                ):
                    row = backup_conn.execute(
                        f"SELECT sentinel FROM {table_name} WHERE id = 1"
                    ).fetchone()
                    assert (
                        row[0] if row else None
                    ) == f"retired:{table_name}", (
                        f"The backup should preserve representative {table_name} data"
                    )
        with sqlite3.connect(retired_map_db) as conn:
            assert migration_versions(conn, "chat_sessions") == [
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                8,
                9,
                10,
            ], "A database already at map migration 4 should apply current migrations"
            assert all(
                not table_exists(conn, table_name)
                for table_name in (
                    "chat_session_map_revisions",
                    "chat_session_map_maintenance",
                    "chat_session_map_attempts",
                )
            ), "The teardown migration should remove every retired map table"
        assert all(
            target.backup_path is None for target in second.targets
        ), "Second run should not create backups when no migrations are pending"
    await app.start()
    rows = activity_data(app.root / "migration-system")
    migration_rows = [
        row
        for row in rows
        if row.get("event", "").startswith("system_database_migrations_")
    ]
    assert migration_rows
    assert all(row.get("operation_id") and row.get("status") for row in migration_rows)
    assert any(row["status"] == "completed" for row in migration_rows)
    assert_operation_pairs(migration_rows)


async def test_system_migration_failure_phases_are_correlated_and_sanitized(
    app: Application,
) -> None:
    app.vault("MigrationFailurePhases")
    await app.start()
    backup_root = app.root / "backup-failure"
    backup_root.mkdir()
    create_legacy_chat_sessions_db(backup_root / "chat_sessions.db")
    (backup_root / MIGRATION_BACKUP_DIRECTORY).write_text("filesystem obstruction")
    with pytest.raises(FileExistsError):
        run_system_migrations(backup_root)
    apply_root = app.root / "apply-failure"
    apply_root.mkdir()
    create_legacy_chat_sessions_db(apply_root / "chat_sessions.db")
    private_marker = "PRIVATE_MIGRATION_FAILURE_SENTINEL"
    with sqlite3.connect(apply_root / "chat_sessions.db") as conn:
        conn.execute(
            "CREATE TABLE schema_migrations(namespace TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, applied_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(namespace, version))"
        )
        conn.execute(
            "CREATE TRIGGER reject_migration BEFORE INSERT ON schema_migrations BEGIN SELECT RAISE(ABORT, 'PRIVATE_MIGRATION_FAILURE_SENTINEL'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        run_system_migrations(apply_root)
    roots = {str(backup_root), str(apply_root)}
    rows = [
        row
        for row in activity_data(app.root / "system")
        if row.get("system_root") in roots
        and row.get("event", "").startswith("system_database_migrations_")
    ]
    failed = [row for row in rows if row["status"] == "failed"]
    assert len(failed) == 2
    backup_failure = next(row for row in failed if row["phase"] == "backup")
    apply_failure = next(row for row in failed if row["phase"] == "apply")
    assert backup_failure["error_type"] == "FileExistsError" and backup_failure["error"]
    assert apply_failure["database_name"] == "chat_sessions"
    assert apply_failure["error_type"] == "IntegrityError" and apply_failure["error"]
    assert_operation_pairs(rows)
    assert private_marker not in json.dumps(activity_data(app.root / "system"))
    app.evidence.save("migration-failure-events.json", rows)
