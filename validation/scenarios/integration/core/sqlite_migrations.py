"""Integration scenario for the lightweight SQLite migration runner."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.database_migrations import SQLiteMigration, apply_sqlite_migrations
from validation.support.base_scenario import BaseScenario


class SQLiteMigrationsScenario(BaseScenario):
    """Validate migration application, skipping, validation, and rollback."""

    async def test_scenario(self):
        db_path = self.artifacts_dir / "sqlite_migrations.db"
        conn = sqlite3.connect(db_path)
        try:
            migrations = (
                SQLiteMigration(
                    version=2,
                    name="insert_probe_row",
                    apply=lambda connection: connection.execute(
                        "INSERT INTO probe (value) VALUES ('applied')"
                    ),
                ),
                SQLiteMigration(
                    version=1,
                    name="create_probe_table",
                    apply=lambda connection: connection.execute(
                        "CREATE TABLE probe (value TEXT NOT NULL)"
                    ),
                ),
            )

            first = apply_sqlite_migrations(
                conn, namespace="validation", migrations=migrations
            )
            self.soft_assert_equal(
                first.applied, (1, 2), "Migrations should apply in version order"
            )
            self.soft_assert_equal(
                first.skipped, (), "First migration run should not skip versions"
            )

            values = [
                row[0] for row in conn.execute("SELECT value FROM probe").fetchall()
            ]
            self.soft_assert_equal(
                values, ["applied"], "Applied migration should mutate the target DB"
            )

            second = apply_sqlite_migrations(
                conn, namespace="validation", migrations=migrations
            )
            self.soft_assert_equal(
                second.applied, (), "Second migration run should not reapply versions"
            )
            self.soft_assert_equal(
                second.skipped,
                (1, 2),
                "Second migration run should skip applied versions",
            )

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
                self.soft_assert(
                    "duplicate migration version" in str(exc),
                    "Duplicate migration versions should be rejected",
                )
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
                self.soft_assert_equal(
                    str(exc),
                    "intentional migration failure",
                    "Failure should propagate",
                )
            else:
                raise AssertionError("Failing migration should raise")

            values_after_failure = [
                row[0]
                for row in conn.execute(
                    "SELECT value FROM probe ORDER BY rowid"
                ).fetchall()
            ]
            self.soft_assert_equal(
                values_after_failure,
                ["applied"],
                "Failed migration should roll back its target-table changes",
            )

            recorded_versions = [row[0] for row in conn.execute("""
                    SELECT version
                    FROM schema_migrations
                    WHERE namespace = 'validation'
                    ORDER BY version
                    """).fetchall()]
            self.soft_assert_equal(
                recorded_versions,
                [1, 2],
                "Failed migration should not be recorded",
            )
        finally:
            conn.close()
            self.teardown_scenario()
