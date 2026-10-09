"""Integration scenario for registered system database migrations."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-system-migrations-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.chat import ChatStore  # noqa: E402
from core.ingestion.service import IngestionService  # noqa: E402
from core.migration_backups import MIGRATION_BACKUP_DIRECTORY  # noqa: E402
from core.runtime.paths import set_bootstrap_roots  # noqa: E402
from core.system_migrations import (  # noqa: E402
    MIGRATION_TARGETS,
    get_system_migration_status,
    run_system_migrations,
)
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SystemDatabaseMigrationsScenario(BaseScenario):
    """Validate release migration status, execution, and backup creation."""

    async def test_scenario(self):
        system_root = self.artifacts_dir / "system"
        system_root.mkdir(parents=True, exist_ok=True)
        chat_db = system_root / "chat_sessions.db"
        self._create_legacy_chat_sessions_db(chat_db)
        ingestion_db = system_root / "ingestion_jobs.db"
        self._create_legacy_ingestion_jobs_db(ingestion_db)
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
        set_bootstrap_roots(self.artifacts_dir / "data", system_root)
        IngestionService()
        with sqlite3.connect(ingestion_db) as conn:
            columns_before_migration = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(ingestion_jobs)")
            }
        self.soft_assert(
            "selected_strategy" not in columns_before_migration,
            "Ingestion service initialization must not bypass managed migrations",
        )
        before = get_system_migration_status(system_root)
        self.soft_assert_equal(
            before.pending_count,
            pending_before_store_initialization.pending_count,
            "Store initialization should not apply registered release migrations",
        )

        after = run_system_migrations(system_root, backup=True)
        self.soft_assert_equal(
            after.pending_count, 0, "Registered migrations should be applied"
        )
        self.soft_assert(
            not legacy_backup.exists(),
            "A migration run should remove managed legacy backups from the system root",
        )
        self.soft_assert_equal(
            existing_legacy_backup.read_bytes(),
            b"existing migration backup",
            "A migration run should not overwrite an existing organized backup",
        )
        self.soft_assert_equal(
            second_existing_legacy_backup.read_bytes(),
            b"second existing migration backup",
            "A migration run should preserve every existing numbered backup",
        )
        self.soft_assert_equal(
            (backup_directory / f"{legacy_backup.name} (3)").read_bytes(),
            b"legacy migration backup",
            "A migration run should choose the next free backup version",
        )

        target_by_db = {target.db_name: target for target in after.targets}
        chat_target = target_by_db["chat_sessions"]
        summary_target = target_by_db["session_summaries"]
        goal_target = target_by_db["goal_ops"]
        vault_target = target_by_db["vault_state"]
        workflow_runs_target = target_by_db["workflow_runs"]
        ingestion_target = target_by_db["ingestion_jobs"]
        self.soft_assert(
            chat_target.backup_path is not None, "Existing chat DB should be backed up"
        )
        self.soft_assert(
            summary_target.backup_path is None,
            "Absent retired summary DB should not create a backup",
        )
        self.soft_assert(
            not summary_target.exists and not summary_target.pending_versions,
            "Fresh installations must not create a retired summary database",
        )
        self.soft_assert(
            goal_target.backup_path is None,
            "New goal_ops DB should not create an empty backup",
        )
        self.soft_assert(
            vault_target.backup_path is None,
            "New vault-state DB should not create an empty backup",
        )
        self.soft_assert(
            workflow_runs_target.backup_path is None,
            "New workflow-runs DB should not create an empty backup",
        )
        self.soft_assert(
            ingestion_target.backup_path is not None,
            "Existing ingestion jobs DB should be backed up",
        )
        if chat_target.backup_path:
            self.soft_assert(
                Path(chat_target.backup_path).exists(), "Chat DB backup should exist"
            )
            self.soft_assert_equal(
                Path(chat_target.backup_path).parent,
                backup_directory,
                "New database backups should be isolated from live system databases",
            )

        with sqlite3.connect(chat_db) as conn:
            self.soft_assert(
                self._table_exists(conn, "chat_compaction_checkpoints"),
                "Chat checkpoint table should exist after migration",
            )
            self.soft_assert_equal(
                self._migration_versions(conn, "chat_sessions"),
                [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                "Chat migration version should be recorded",
            )
            checkpoint_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(chat_compaction_checkpoints)"
                )
            }
            self.soft_assert(
                "replacement_source_sequence_indexes_json" in checkpoint_columns,
                "Chat checkpoint migration should add replacement origins",
            )
            self.soft_assert(
                not self._table_exists(conn, "chat_session_map_revisions"),
                "Retired session-map revision storage should be absent after migration",
            )
            self.soft_assert(
                not self._table_exists(conn, "chat_session_map_maintenance"),
                "Retired session-map maintenance storage should be absent after migration",
            )
            self.soft_assert(
                not self._table_exists(conn, "chat_session_map_attempts"),
                "Retired session-map attempt storage should be absent after migration",
            )
            owner = conn.execute(
                "SELECT owner_principal_id FROM chat_sessions WHERE session_id = 'legacy'"
            ).fetchone()
            self.soft_assert_equal(
                owner[0] if owner else None,
                "local-user",
                "Legacy chat sessions should be assigned to the local user",
            )

        with sqlite3.connect(system_root / "goal_ops.db") as conn:
            self.soft_assert(
                self._table_exists(conn, "goals"),
                "goal_ops goals table should exist after migration",
            )
            self.soft_assert_equal(
                self._migration_versions(conn, "goal_ops"),
                [1, 2, 3],
                "goal_ops migration version should be recorded",
            )

        with sqlite3.connect(system_root / "vault_state.db") as conn:
            self.soft_assert_equal(
                self._migration_versions(conn, "vault_state"),
                [1],
                "Vault-state migration version should be recorded",
            )

        with sqlite3.connect(system_root / "workflow_runs.db") as conn:
            self.soft_assert(
                self._table_exists(conn, "workflow_runs"),
                "Workflow run history table should exist after migration",
            )
            self.soft_assert_equal(
                self._migration_versions(conn, "workflow_runs"),
                [1, 2, 3],
                "Workflow run migration versions should be recorded",
            )

        with sqlite3.connect(ingestion_db) as conn:
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(ingestion_jobs)")
            }
            self.soft_assert(
                {
                    "selected_strategy",
                    "selected_provider",
                    "selected_model",
                    "strategy_attempts",
                    "fallback_reason",
                }.issubset(columns),
                "Ingestion provenance columns should be added",
            )
            self.soft_assert_equal(
                self._migration_versions(conn, "ingestion_jobs"),
                [1],
                "Ingestion migration version should be recorded",
            )

        second = run_system_migrations(system_root, backup=True)
        self.soft_assert_equal(
            second.pending_count, 0, "Second run should remain fully applied"
        )

        retired_map_root = self.artifacts_dir / "retired-map-system"
        retired_map_root.mkdir()
        retired_map_db = retired_map_root / "chat_sessions.db"
        self._create_pre_retirement_chat_sessions_db(retired_map_db)
        retired_map_migrations = run_system_migrations(
            retired_map_root,
            backup=True,
        )
        retired_map_chat_target = next(
            target
            for target in retired_map_migrations.targets
            if target.db_name == "chat_sessions"
        )
        self.soft_assert(
            retired_map_chat_target.backup_path is not None,
            "A pre-retirement chat database should be backed up before map teardown",
        )
        if retired_map_chat_target.backup_path is not None:
            backup_path = Path(retired_map_chat_target.backup_path)
            self.soft_assert(
                backup_path.exists(), "The pre-retirement backup should exist"
            )
            with sqlite3.connect(backup_path) as backup_conn:
                for table_name in (
                    "chat_session_map_revisions",
                    "chat_session_map_maintenance",
                    "chat_session_map_attempts",
                ):
                    row = backup_conn.execute(
                        f"SELECT sentinel FROM {table_name} WHERE id = 1"
                    ).fetchone()
                    self.soft_assert_equal(
                        row[0] if row else None,
                        f"retired:{table_name}",
                        f"The backup should preserve representative {table_name} data",
                    )
        with sqlite3.connect(retired_map_db) as conn:
            self.soft_assert_equal(
                self._migration_versions(conn, "chat_sessions"),
                [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                "A database already at map migration 4 should apply current migrations",
            )
            self.soft_assert(
                all(
                    not self._table_exists(conn, table_name)
                    for table_name in (
                        "chat_session_map_revisions",
                        "chat_session_map_maintenance",
                        "chat_session_map_attempts",
                    )
                ),
                "The teardown migration should remove every retired map table",
            )
        self.soft_assert(
            all(target.backup_path is None for target in second.targets),
            "Second run should not create backups when no migrations are pending",
        )
        private_marker = "PRIVATE_MIGRATION_FAILURE_SENTINEL"
        with patch(
            "core.system_migrations._backup_pending_databases",
            side_effect=OSError(private_marker),
        ):
            try:
                run_system_migrations(system_root)
            except OSError:
                pass
            else:
                raise AssertionError("A migration backup failure must propagate")
        target = next(
            target for target in MIGRATION_TARGETS if target.db_name == "chat_sessions"
        )

        def fail_schema(_root):
            raise ValueError(private_marker)

        with patch(
            "core.system_migrations.MIGRATION_TARGETS",
            (replace(target, ensure_schema=fail_schema),),
        ):
            try:
                run_system_migrations(system_root)
            except ValueError:
                pass
            else:
                raise AssertionError("A migration apply failure must propagate")
        rows = [
            json.loads(line)["data"]
            for line in (system_root / "activity.log").read_text().splitlines()
        ]
        migration_rows = [
            row
            for row in rows
            if row.get("event", "").startswith("system_database_migrations_")
        ]
        assert migration_rows
        assert all(
            row.get("operation_id") and row.get("status") for row in migration_rows
        )
        completed = [row for row in migration_rows if row["status"] == "completed"]
        failed = [row for row in migration_rows if row["status"] == "failed"]
        assert completed and len(failed) == 2
        backup_failure = next(row for row in failed if row["phase"] == "backup")
        apply_failure = next(row for row in failed if row["phase"] == "apply")
        assert backup_failure["error_type"] == "OSError" and backup_failure["error"]
        assert apply_failure["database_name"] == "chat_sessions"
        assert apply_failure["error_type"] == "ValueError" and apply_failure["error"]
        for operation_id in {row["operation_id"] for row in migration_rows}:
            operation = [
                row for row in migration_rows if row["operation_id"] == operation_id
            ]
            assert len([row for row in operation if row["status"] == "started"]) == 1
            assert (
                len(
                    [
                        row
                        for row in operation
                        if row["status"] in {"completed", "failed"}
                    ]
                )
                == 1
            )
        assert private_marker not in json.dumps(rows)
        self.teardown_scenario()
        self.assert_no_failures()

    @staticmethod
    def _create_legacy_chat_sessions_db(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE chat_sessions (
                    session_id TEXT NOT NULL,
                    vault_name TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_activity_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    title TEXT,
                    metadata_json TEXT,
                    PRIMARY KEY (session_id, vault_name)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO chat_sessions (session_id, vault_name)
                VALUES ('legacy', 'MigrationVault')
                """
            )

    @staticmethod
    def _create_pre_retirement_chat_sessions_db(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE schema_migrations (
                    namespace TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (namespace, version)
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO schema_migrations (namespace, version, name)
                VALUES ('chat_sessions', ?, ?)
                """,
                (
                    (1, "add_compaction_checkpoints"),
                    (2, "add_session_owner_principal"),
                    (3, "add_live_session_map_storage"),
                    (4, "add_live_session_map_attempt_audit"),
                ),
            )
            for table_name in (
                "chat_session_map_revisions",
                "chat_session_map_maintenance",
                "chat_session_map_attempts",
            ):
                conn.execute(
                    f"CREATE TABLE {table_name} (id INTEGER PRIMARY KEY, sentinel TEXT NOT NULL)"
                )
                conn.execute(
                    f"INSERT INTO {table_name} (id, sentinel) VALUES (1, ?)",
                    (f"retired:{table_name}",),
                )

    @staticmethod
    def _create_legacy_ingestion_jobs_db(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE ingestion_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_uri VARCHAR NOT NULL,
                    vault VARCHAR,
                    source_type VARCHAR NOT NULL,
                    mime_hint VARCHAR,
                    options JSON,
                    status VARCHAR NOT NULL,
                    error TEXT,
                    outputs JSON,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )

    @staticmethod
    def _migration_versions(conn: sqlite3.Connection, namespace: str) -> list[int]:
        return [
            int(row[0])
            for row in conn.execute(
                """
                SELECT version
                FROM schema_migrations
                WHERE namespace = ?
                ORDER BY version
                """,
                (namespace,),
            ).fetchall()
        ]

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
        return row is not None


if __name__ == "__main__":
    asyncio.run(SystemDatabaseMigrationsScenario().test_scenario())
