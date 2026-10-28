"""Integration scenario for workflow failure vault-state rollback."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.vault_state.file_mutations import VaultMutationRejected
from core.vault_state.rollback import rollback_task_file_mutations
from validation.support.base_scenario import BaseScenario


class VaultStateRollbackScenario(BaseScenario):
    """Validate failed workflow tasks rollback recorded vault file mutations."""

    async def test_scenario(self):
        vault = self.create_vault("VaultStateRollbackVault")
        self.create_file(vault, "notes/preexisting-append.md", "Original append\n")
        self.create_file(vault, "notes/preexisting-delete.md", "Original delete\n")
        self.create_file(vault, "notes/move-source.md", "Original move source\n")
        self.create_file(
            vault, "AssistantMD/Authoring/failing_probe.md", FAILING_PROBE_WORKFLOW
        )

        await self.start_system()

        checkpoint = self.event_checkpoint()
        result = await self.run_workflow(vault, "failing_probe", expect_failure=True)
        events = self.events_since(checkpoint)

        self.soft_assert_equal(
            result.status, "failed", "Workflow failure should be reported"
        )
        failed_event = self.assert_event_contains(
            events,
            name="workflow_task_failed",
            expected={
                "workflow_id": f"{vault.name}/failing_probe",
                "status": "failed",
                "failure_kind": "unknown",
                "retryable": False,
            },
        )
        task_id = failed_event["data"]["task_id"]
        task_detail = self.call_api(f"/api/tasks/{task_id}")
        assert (
            task_detail.status_code == 200
        ), "Failed workflow task detail should be available"
        workflow_failure = (
            task_detail.json().get("metadata", {}).get("workflow_failure")
        )
        self.soft_assert(
            isinstance(workflow_failure, dict),
            "Failed workflow task should expose structured recovery metadata",
        )
        if isinstance(workflow_failure, dict):
            self.soft_assert_equal(
                workflow_failure.get("failure_kind"),
                "unknown",
                "Unexpected workflow exceptions should preserve fail-fast unknown classification",
            )
            self.soft_assert_equal(
                workflow_failure.get("retryable"),
                False,
                "Unexpected workflow exceptions should not be marked retryable",
            )
            self.soft_assert_equal(
                workflow_failure.get("workflow_id"),
                f"{vault.name}/failing_probe",
                "Workflow failure metadata should identify the failed workflow",
            )
            self.soft_assert(
                isinstance(workflow_failure.get("recovery_summary"), dict),
                "Workflow failure metadata should include a compact recovery summary",
            )
        self.assert_event_contains(
            events,
            name="task_rollback_started",
            expected={
                "task_id": task_id,
                "terminal_status": "failed",
            },
        )
        self.assert_event_contains(
            events,
            name="task_rollback_completed",
            expected={
                "task_id": task_id,
                "terminal_status": "failed",
                "rollback_status": "partial",
                "nonrollbackable_mutation_rows": 1,
            },
        )

        self.soft_assert(
            not (Path(vault) / "notes/created-before-failure.md").exists(),
            "Rollback should delete files created by the failed workflow",
        )
        self.soft_assert_equal(
            (Path(vault) / "notes/preexisting-append.md").read_text(encoding="utf-8"),
            "Original append\n",
            "Rollback should restore appended file content",
        )
        self.soft_assert_equal(
            (Path(vault) / "notes/preexisting-delete.md").read_text(encoding="utf-8"),
            "Original delete\n",
            "Rollback should restore deleted file content",
        )
        self.soft_assert_equal(
            (Path(vault) / "notes/move-source.md").read_text(encoding="utf-8"),
            "Original move source\n",
            "Rollback should restore moved source content",
        )
        self.soft_assert(
            not (Path(vault) / "notes/move-destination.md").exists(),
            "Rollback should remove moved destination file",
        )
        self.soft_assert(
            (Path(vault) / "notes/directory-before-failure").is_dir(),
            "Directory actions should remain when task rollback has no retained directory snapshot",
        )

        snapshot_status = self._snapshot_status(task_id)
        self.soft_assert_equal(
            snapshot_status, "rolled_back", "Task snapshot should be marked rolled back"
        )
        self.soft_assert_equal(
            self._activity_status(task_id),
            ("failed", "partial"),
            "Task activity should expose a partial rollback when directory actions remain",
        )
        retry_result = rollback_task_file_mutations(
            task_id=task_id,
            terminal_status="failed",
            reason="validation retry",
        )
        self.soft_assert(retry_result.skipped, "Second rollback should be skipped")
        self.soft_assert_equal(
            retry_result.reason,
            "already_rolled_back",
            "Second rollback should report already rolled back",
        )
        self.soft_assert_equal(
            retry_result.mutation_rows_seen,
            len(self._mutation_rows(task_id)),
            "Second rollback should still report retained mutation rows",
        )
        self.soft_assert(
            not (Path(vault) / "notes/created-before-failure.md").exists(),
            "Second rollback should leave created file deleted",
        )
        self.soft_assert_equal(
            (Path(vault) / "notes/preexisting-append.md").read_text(encoding="utf-8"),
            "Original append\n",
            "Second rollback should not change restored append content",
        )

        self._prepare_conflicting_retry(task_id)
        conflict_path = Path(vault) / "notes/preexisting-append.md"
        untouched_path = Path(vault) / "notes/created-before-failure.md"
        conflict_path.write_text("Later external edit\n", encoding="utf-8")
        untouched_path.write_text("created then rolled back\n", encoding="utf-8")
        try:
            rollback_task_file_mutations(
                task_id=task_id,
                terminal_status="failed",
                reason="validation conflict retry",
            )
        except VaultMutationRejected as exc:
            self.soft_assert_equal(
                exc.code,
                "file_conflict",
                "Task rollback should report a stale current state",
            )
        else:
            self.soft_assert(False, "Task rollback should reject a later file edit")
        self.soft_assert_equal(
            conflict_path.read_text(encoding="utf-8"),
            "Later external edit\n",
            "Rejected task rollback must preserve the later file edit",
        )
        self.soft_assert_equal(
            untouched_path.read_text(encoding="utf-8"),
            "created then rolled back\n",
            "A conflict must prevent rollback of every other path in the vault",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _snapshot_status(self, task_id: str) -> str | None:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT status FROM snapshot_sets WHERE task_id = ? AND purpose = 'rollback'",
                (task_id,),
            ).fetchone()
            return row[0] if row is not None else None
        finally:
            conn.close()

    def _mutation_rows(self, task_id: str) -> list[tuple]:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute(
                """
                SELECT m.id
                FROM vault_mutations AS m
                JOIN vault_activities AS a ON a.activity_id = m.activity_id
                WHERE a.task_id = ?
                """,
                (task_id,),
            ).fetchall()
        finally:
            conn.close()

    def _activity_status(self, task_id: str) -> tuple[str, str | None] | None:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT status, rollback_status FROM vault_activities WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return (row[0], row[1]) if row is not None else None
        finally:
            conn.close()

    def _prepare_conflicting_retry(self, task_id: str) -> None:
        """Retain one mutation group and make its task snapshot eligible again."""
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            activity_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT activity_id FROM vault_activities WHERE task_id = ?",
                    (task_id,),
                )
            ]
            placeholders = ",".join("?" for _ in activity_ids)
            conn.execute(
                f"DELETE FROM vault_mutations WHERE activity_id IN ({placeholders}) AND path NOT IN (?, ?)",
                (
                    *activity_ids,
                    "notes/preexisting-append.md",
                    "notes/created-before-failure.md",
                ),
            )
            conn.execute(
                "UPDATE snapshot_sets SET status = 'active', rolled_back_at = NULL WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()


FAILING_PROBE_WORKFLOW = """---
run_type: workflow
enabled: false
description: Vault-state rollback failure probe
---

## Run

```python
await file_write(
    operation="write",
    path="notes/created-before-failure.md",
    content="created then rolled back\\n",
)
await file_write(
    operation="append",
    path="notes/preexisting-append.md",
    content="mutated append\\n",
)
await file_write(
    operation="delete",
    path="notes/preexisting-delete.md",
    confirm_path="notes/preexisting-delete.md",
)
await file_write(
    operation="move",
    path="notes/move-source.md",
    destination="notes/move-destination.md",
)
await file_write(
    operation="mkdir",
    path="notes/directory-before-failure",
)
raise RuntimeError("rollback probe failure")
```
"""
