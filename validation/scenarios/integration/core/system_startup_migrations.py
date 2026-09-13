"""Validate runtime startup applies system database migrations before use."""

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
        prefix="assistantmd-system-startup-migrations-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.system_migrations import get_system_migration_status  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SystemStartupMigrationsScenario(BaseScenario):
    """Validate startup migrates existing system databases before loading runtime services."""

    async def test_scenario(self):
        self.create_vault("StartupMigrationVault")
        system_root = self._get_system_controller()._system_root
        self._create_legacy_chat_sessions_db(system_root / "chat_sessions.db")
        self._create_legacy_session_summaries_db(system_root / "session_summaries.db")
        self._create_goal_ops_v1_db(system_root / "goal_ops.db")

        before = get_system_migration_status(system_root)
        self.soft_assert(
            before.pending_count > 0,
            "Legacy startup databases should have pending migrations before bootstrap",
        )

        await self.start_system()

        after = get_system_migration_status(system_root)
        self.soft_assert_equal(
            after.pending_count,
            0,
            "Startup should apply all system database migrations",
        )

        with sqlite3.connect(system_root / "chat_sessions.db") as conn:
            self.soft_assert(
                self._table_exists(conn, "chat_compaction_checkpoints"),
                "Startup should migrate chat compaction checkpoint storage",
            )
            self.soft_assert_equal(
                self._migration_versions(conn, "chat_sessions"),
                [1, 2],
                "Startup should record chat migration versions",
            )

        with sqlite3.connect(system_root / "session_summaries.db") as conn:
            summary_columns = self._table_columns(conn, "session_summaries")
            self.soft_assert(
                "source_summary" in summary_columns,
                "Startup should migrate summary source text",
            )
            self.soft_assert(
                "workspace_path" in summary_columns,
                "Startup should migrate summary workspace paths",
            )
            self.soft_assert(
                self._table_exists(conn, "session_summaries_fts"),
                "Startup should ensure summary FTS storage",
            )
            self.soft_assert_equal(
                self._migration_versions(conn, "session_summaries"),
                [1, 2, 3],
                "Startup should record summary migration versions",
            )

        with sqlite3.connect(system_root / "goal_ops.db") as conn:
            goal_columns = self._table_columns(conn, "goals")
            self.soft_assert(
                "source_type" in goal_columns,
                "Startup should migrate goal source provenance",
            )
            self.soft_assert(
                "source_id" in goal_columns, "Startup should migrate goal source ids"
            )
            self.soft_assert(
                "source_task_id" in goal_columns,
                "Startup should migrate goal source task ids",
            )
            self.soft_assert(
                "source_label" in goal_columns,
                "Startup should migrate goal source labels",
            )
            self.soft_assert(
                "plan_json" in goal_columns,
                "Startup should migrate goal plan snapshots",
            )
            self.soft_assert_equal(
                self._migration_versions(conn, "goal_ops"),
                [1, 2, 3],
                "Startup should preserve existing goal migration history and apply pending versions",
            )

        with sqlite3.connect(system_root / "vault_state.db") as conn:
            self.soft_assert_equal(
                self._migration_versions(conn, "vault_state"),
                [1],
                "Startup should record the vault-state migration version",
            )

        with sqlite3.connect(system_root / "workflow_runs.db") as conn:
            self.soft_assert(
                self._table_exists(conn, "workflow_runs"),
                "Startup should create durable workflow run history",
            )
            self.soft_assert_equal(
                self._migration_versions(conn, "workflow_runs"),
                [1, 2, 3],
                "Startup should record the workflow-run migration versions",
            )

        await self.stop_system()
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

    @staticmethod
    def _create_legacy_session_summaries_db(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE session_summaries (
                    session_id TEXT NOT NULL,
                    vault_name TEXT NOT NULL,
                    title TEXT,
                    summary TEXT,
                    domain TEXT,
                    work_product TEXT,
                    user_intent TEXT,
                    named_entities TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    metadata_json TEXT,
                    PRIMARY KEY (session_id, vault_name)
                )
                """
            )

    @staticmethod
    def _create_goal_ops_v1_db(db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE goals (
                    goal_id TEXT PRIMARY KEY,
                    vault_name TEXT NOT NULL,
                    workspace_path_hint TEXT,
                    title TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    success_criteria_json TEXT NOT NULL DEFAULT '[]',
                    metadata_json TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
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
            conn.execute(
                """
                INSERT INTO schema_migrations (namespace, version, name)
                VALUES ('goal_ops', 1, 'create_goal_ops_tables')
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
            WHERE type IN ('table', 'virtual table')
              AND name = ?
            LIMIT 1
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
        return {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }


if __name__ == "__main__":
    asyncio.run(SystemStartupMigrationsScenario().test_scenario())
