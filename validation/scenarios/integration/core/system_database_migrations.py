"""Integration scenario for registered system database migrations."""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import tempfile
from pathlib import Path

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
            "New summary DB should not create an empty backup",
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
                [1, 2],
                "Chat migration version should be recorded",
            )
            owner = conn.execute(
                "SELECT owner_principal_id FROM chat_sessions WHERE session_id = 'legacy'"
            ).fetchone()
            self.soft_assert_equal(
                owner[0] if owner else None,
                "local-user",
                "Legacy chat sessions should be assigned to the local user",
            )

        with sqlite3.connect(system_root / "session_summaries.db") as conn:
            self.soft_assert_equal(
                self._migration_versions(conn, "session_summaries"),
                [1, 2, 3],
                "Session summary migration versions should be recorded",
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
        self.soft_assert(
            all(target.backup_path is None for target in second.targets),
            "Second run should not create backups when no migrations are pending",
        )
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
