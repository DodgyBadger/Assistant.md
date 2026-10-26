"""Integration scenario for manual vault-state cleanup."""

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario


class VaultStateCleanupScenario(BaseScenario):
    """Validate cleanup deletes only expired vault-state safety artifacts."""

    async def test_scenario(self):
        vault = self.create_vault("VaultStateCleanupVault")
        self.create_file(vault, "notes/keep.md", "Do not delete vault content\n")

        await self.start_system()

        from core.vault_state import VaultStateService

        service = VaultStateService()
        refresh = service.refresh_vault(vault)
        now = datetime.now(UTC)
        expired_at = now - timedelta(days=1)
        retained_at = now + timedelta(days=1)
        system_root = self._get_system_controller()._system_root
        expired_snapshot_root = system_root / "vault_snapshots" / "9001"
        retained_snapshot_root = system_root / "vault_snapshots" / "9002"
        expired_file = expired_snapshot_root / "files" / "notes" / "expired.md"
        retained_file = retained_snapshot_root / "files" / "notes" / "retained.md"
        expired_file.parent.mkdir(parents=True, exist_ok=True)
        retained_file.parent.mkdir(parents=True, exist_ok=True)
        expired_file.write_text("expired snapshot\n", encoding="utf-8")
        retained_file.write_text("retained snapshot\n", encoding="utf-8")
        self._insert_cleanup_rows(
            vault_id=refresh.vault_id,
            vault_name=vault.name,
            expired_at=expired_at,
            retained_at=retained_at,
            expired_snapshot_root=expired_snapshot_root,
            retained_snapshot_root=retained_snapshot_root,
        )

        checkpoint = self.event_checkpoint()
        response = self.call_api("/api/vault-state/cleanup", method="POST")
        events = self.events_since(checkpoint)

        self.soft_assert_equal(
            response.status_code, 200, "Cleanup endpoint should respond"
        )
        payload = response.json()
        self.soft_assert_equal(payload.get("success"), True, "Cleanup should succeed")
        self.soft_assert_equal(
            payload.get("expired_activity_rows_deleted"),
            1,
            "Cleanup should delete expired activity rows",
        )
        self.soft_assert_equal(
            payload.get("expired_mutation_rows_deleted"),
            1,
            "Cleanup should delete expired mutation rows",
        )
        self.soft_assert_equal(
            payload.get("expired_snapshot_rows_deleted"),
            1,
            "Cleanup should delete expired snapshot rows",
        )
        self.soft_assert_equal(
            payload.get("snapshot_files_deleted"),
            1,
            "Cleanup should delete expired snapshot files",
        )
        self.soft_assert(
            payload.get("snapshot_dirs_deleted", 0) >= 1,
            "Cleanup should delete expired snapshot directories",
        )
        self.assert_event_contains(
            events,
            name="vault_state_cleanup_completed",
            expected={
                "expired_activity_rows_deleted": 1,
                "expired_mutation_rows_deleted": 1,
                "expired_snapshot_rows_deleted": 1,
                "snapshot_files_deleted": 1,
            },
        )

        remaining_mutations = self._mutation_task_ids()
        remaining_snapshots = self._snapshot_task_ids()
        self.soft_assert_equal(
            "expired-task" in remaining_mutations,
            False,
            "Expired mutation row should be removed",
        )
        self.soft_assert_equal(
            "retained-task" in remaining_mutations,
            True,
            "Unexpired mutation row should remain",
        )
        self.soft_assert_equal(
            "expired-task" in remaining_snapshots,
            False,
            "Expired snapshot row should be removed",
        )
        self.soft_assert_equal(
            "retained-task" in remaining_snapshots,
            True,
            "Unexpired snapshot row should remain",
        )
        self.soft_assert_equal(
            expired_snapshot_root.exists(),
            False,
            "Expired snapshot directory should be deleted",
        )
        self.soft_assert_equal(
            retained_file.exists(),
            True,
            "Unexpired snapshot file should remain",
        )
        self.soft_assert_equal(
            (vault / "notes" / "keep.md").exists(),
            True,
            "Cleanup must not delete vault files",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _insert_cleanup_rows(
        self,
        *,
        vault_id: str,
        vault_name: str,
        expired_at: datetime,
        retained_at: datetime,
        expired_snapshot_root: Path,
        retained_snapshot_root: Path,
    ) -> None:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.executemany(
                """
                INSERT INTO vault_activities (
                    activity_id, vault_id, vault_name, kind, source, scope, label,
                    task_id, status, created_at, updated_at, completed_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "task:expired-task:" + vault_id,
                        vault_id,
                        vault_name,
                        "workflow",
                        "api",
                        "workflow_vault:" + vault_name,
                        vault_name + "/expired",
                        "expired-task",
                        "completed",
                        expired_at.isoformat(),
                        expired_at.isoformat(),
                        expired_at.isoformat(),
                        expired_at.isoformat(),
                    ),
                    (
                        "task:retained-task:" + vault_id,
                        vault_id,
                        vault_name,
                        "workflow",
                        "api",
                        "workflow_vault:" + vault_name,
                        vault_name + "/retained",
                        "retained-task",
                        "completed",
                        retained_at.isoformat(),
                        retained_at.isoformat(),
                        retained_at.isoformat(),
                        retained_at.isoformat(),
                    ),
                ],
            )
            conn.executemany(
                """
                INSERT INTO vault_mutations (
                    activity_id, operation_id, path, target_kind, operation, status,
                    before_exists, before_hash, after_exists, after_hash,
                    created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        "task:expired-task:" + vault_id,
                        "expired-operation",
                        "notes/expired.md",
                        "file",
                        "write",
                        "completed",
                        0,
                        None,
                        1,
                        "expired-hash",
                        expired_at.isoformat(),
                        expired_at.isoformat(),
                    ),
                    (
                        "task:retained-task:" + vault_id,
                        "retained-operation",
                        "notes/retained.md",
                        "file",
                        "write",
                        "completed",
                        0,
                        None,
                        1,
                        "retained-hash",
                        retained_at.isoformat(),
                        retained_at.isoformat(),
                    ),
                ],
            )
            conn.executemany(
                """
                INSERT INTO snapshot_sets (
                    id, task_id, task_kind, task_source, task_scope, task_label,
                    vault_id, vault_name, purpose, scope_kind, scope_id, snapshot_root,
                    status, created_at, expires_at, rolled_back_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        9001,
                        "expired-task",
                        "workflow",
                        "api",
                        "workflow_vault:" + vault_name,
                        vault_name + "/expired",
                        vault_id,
                        vault_name,
                        "rollback",
                        "task",
                        "expired-task",
                        str(expired_snapshot_root),
                        "active",
                        expired_at.isoformat(),
                        expired_at.isoformat(),
                        None,
                    ),
                    (
                        9002,
                        "retained-task",
                        "workflow",
                        "api",
                        "workflow_vault:" + vault_name,
                        vault_name + "/retained",
                        vault_id,
                        vault_name,
                        "rollback",
                        "task",
                        "retained-task",
                        str(retained_snapshot_root),
                        "active",
                        retained_at.isoformat(),
                        retained_at.isoformat(),
                        None,
                    ),
                ],
            )
            conn.executemany(
                """
                INSERT INTO file_snapshots (
                    id, snapshot_set_id, task_id, vault_id, vault_name, path, source,
                    file_exists, content_hash, snapshot_ref, created_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        9101,
                        9001,
                        "expired-task",
                        vault_id,
                        vault_name,
                        "notes/expired.md",
                        "task_mutation_before",
                        1,
                        "expired-hash",
                        "files/notes/expired.md",
                        expired_at.isoformat(),
                        expired_at.isoformat(),
                    ),
                    (
                        9102,
                        9002,
                        "retained-task",
                        vault_id,
                        vault_name,
                        "notes/retained.md",
                        "task_mutation_before",
                        1,
                        "retained-hash",
                        "files/notes/retained.md",
                        retained_at.isoformat(),
                        retained_at.isoformat(),
                    ),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def _mutation_task_ids(self) -> set[str]:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("""
                SELECT a.task_id
                FROM vault_mutations AS m
                JOIN vault_activities AS a ON a.activity_id = m.activity_id
                """).fetchall()
            return {str(row[0]) for row in rows}
        finally:
            conn.close()

    def _snapshot_task_ids(self) -> set[str]:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT task_id FROM snapshot_sets").fetchall()
            return {str(row[0]) for row in rows}
        finally:
            conn.close()
