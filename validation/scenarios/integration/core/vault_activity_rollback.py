"""Integration scenario for explicit atomic vault activity rollback."""

import asyncio
import json
import sqlite3
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

import core.vault_state.activity_rollback as activity_rollback
from core.vault_state.activity import VaultActivityContext, use_vault_activity
from core.vault_state.activity_rollback import ActivityRollbackUnavailable
from core.vault_state.file_mutations import (
    replace_vault_file_content,
    write_vault_file,
)
from core.vault_state.service import VaultStateService
from validation.support.base_scenario import BaseScenario


class VaultActivityRollbackScenario(BaseScenario):
    """Validate all-or-nothing rollback and state-based undo chaining."""

    async def test_scenario(self):
        vault = self.create_vault("VaultActivityRollbackVault")
        self.create_file(vault, "notes/existing.md", "original\n")

        await self.start_system()

        source_id = "activity_rollback_source"
        source_context = VaultActivityContext(
            activity_id=source_id,
            kind="chat",
            source="api",
            scope="chat_session:rollback-test",
            label="Update rollback fixtures",
        )
        with use_vault_activity(source_context):
            replace_vault_file_content(
                vault_path=vault,
                path="notes/existing.md",
                content="first update\n",
                operation="replace_text",
            )
            replace_vault_file_content(
                vault_path=vault,
                path="notes/existing.md",
                content="final update\n",
                operation="replace_text",
            )
            write_vault_file(
                vault_path=vault,
                path="notes/created.md",
                content="created\n",
            )
            replace_vault_file_content(
                vault_path=vault,
                path="notes/created.md",
                content="created and edited\n",
                operation="replace_text",
            )
        VaultStateService().finish_activity(activity_id=source_id, status="completed")

        preview = self._preview(vault.name, source_id)
        assert preview["can_rollback"] is True
        assert preview["restore_count"] == 1
        assert preview["delete_count"] == 1
        assert {item["path"] for item in preview["paths"]} == {
            "notes/created.md",
            "notes/existing.md",
        }

        later_id = "activity_rollback_later_edit"
        later_context = VaultActivityContext(
            activity_id=later_id,
            kind="explorer",
            source="api",
            scope=None,
            label="Edit existing.md",
        )
        with use_vault_activity(later_context):
            replace_vault_file_content(
                vault_path=vault,
                path="notes/existing.md",
                content="later manual edit\n",
                operation="update_vault_file",
            )
        VaultStateService().finish_activity(activity_id=later_id, status="completed")

        blocked = self._preview(vault.name, source_id)
        assert blocked["can_rollback"] is False
        assert any(issue["code"] == "state_conflict" for issue in blocked["issues"])
        rejected = self._execute(vault.name, source_id, blocked)
        assert rejected.status_code == 409
        assert (vault / "notes/created.md").read_text(encoding="utf-8") == (
            "created and edited\n"
        ), "A blocked rollback must not change any other path"

        later_preview = self._preview(vault.name, later_id)
        later_rollback = self._execute(vault.name, later_id, later_preview)
        assert later_rollback.status_code == 200
        assert (vault / "notes/existing.md").read_text(
            encoding="utf-8"
        ) == "final update\n"

        available_again = self._preview(vault.name, source_id)
        assert (
            available_again["can_rollback"] is True
        ), "Restoring the later edit should re-enable the earlier activity rollback"
        rollback_checkpoint = self.event_checkpoint()
        source_rollback = self._execute(vault.name, source_id, available_again)
        assert source_rollback.status_code == 200
        source_result = source_rollback.json()
        assert source_result["source_activity_id"] == source_id
        rollback_activity_id = source_result["rollback_activity_id"]
        assert (vault / "notes/existing.md").read_text(encoding="utf-8") == "original\n"
        assert not (vault / "notes/created.md").exists()
        self.assert_event_contains(
            self.events_since(rollback_checkpoint),
            name="vault_activity_rollback_completed",
            expected={
                "source_activity_id": source_id,
                "rollback_activity_id": rollback_activity_id,
                "vault_name": vault.name,
                "restored_count": 1,
                "deleted_count": 1,
            },
        )

        source_after = self._preview(vault.name, source_id)
        assert source_after["can_rollback"] is False
        assert any(
            issue["code"] == "already_rolled_back" for issue in source_after["issues"]
        )

        undo_preview = self._preview(vault.name, rollback_activity_id)
        assert undo_preview["can_rollback"] is True
        undo = self._execute(vault.name, rollback_activity_id, undo_preview)
        assert undo.status_code == 200
        assert (vault / "notes/existing.md").read_text(
            encoding="utf-8"
        ) == "final update\n"
        assert (vault / "notes/created.md").read_text(encoding="utf-8") == (
            "created and edited\n"
        )

        activity_rows = self._activity_rows()
        source_row = next(
            row for row in activity_rows if row["activity_id"] == source_id
        )
        rollback_row = next(
            row for row in activity_rows if row["activity_id"] == rollback_activity_id
        )
        assert source_row["status"] == "completed"
        assert source_row["rollback_status"] == "completed"
        assert (
            json.loads(rollback_row["metadata_json"])["source_activity_id"] == source_id
        )

        directory = self.call_api(
            f"/api/vaults/{vault.name}/paths/mutate",
            method="POST",
            data={"operation": "create_directory", "path": "unsupported-directory"},
        )
        assert directory.status_code == 200
        activity = self.call_api(f"/api/vaults/{vault.name}/activity").json()
        directory_activity = next(
            group
            for group in activity["groups"]
            if any(
                mutation["target_kind"] == "directory"
                and mutation["path"] == "unsupported-directory"
                for mutation in group["mutations"]
            )
        )
        directory_preview = self._preview(vault.name, directory_activity["activity_id"])
        assert directory_preview["can_rollback"] is False
        assert any(
            issue["code"] == "unsupported_directory_operation"
            for issue in directory_preview["issues"]
        )

        await self._assert_concurrent_submission_is_serialized(vault)

    async def _assert_concurrent_submission_is_serialized(self, vault: Path) -> None:
        source_id = "activity_concurrent_rollback_source"
        source_context = VaultActivityContext(
            activity_id=source_id,
            kind="explorer",
            source="api",
            scope=None,
            label="Concurrent rollback fixture",
        )
        self.create_file(vault, "notes/concurrent-rollback.md", "before\n")
        with use_vault_activity(source_context):
            replace_vault_file_content(
                vault_path=vault,
                path="notes/concurrent-rollback.md",
                content="after\n",
                operation="update_vault_file",
            )
        VaultStateService().finish_activity(activity_id=source_id, status="completed")

        preview = activity_rollback.preview_activity_rollback(
            vault_path=vault,
            activity_id=source_id,
        )
        expected_states = tuple(
            (item.path, item.expected_exists, item.expected_sha256)
            for item in preview.paths
        )
        first_entered = threading.Event()
        allow_first = threading.Event()
        restore_calls = 0
        restore_calls_lock = threading.Lock()
        original_restore = activity_rollback.restore_vault_file_states

        def paused_restore(**kwargs):
            nonlocal restore_calls
            with restore_calls_lock:
                restore_calls += 1
                call_number = restore_calls
            if call_number == 1:
                first_entered.set()
                assert allow_first.wait(2), "Timed out waiting to release rollback"
            return original_restore(**kwargs)

        def execute():
            return activity_rollback.execute_activity_rollback(
                vault_path=vault,
                activity_id=source_id,
                expected_states=expected_states,
            )

        activity_rollback.restore_vault_file_states = paused_restore
        try:
            first = asyncio.create_task(asyncio.to_thread(execute))
            assert await asyncio.to_thread(first_entered.wait, 1)
            second = asyncio.create_task(asyncio.to_thread(execute))
            await asyncio.sleep(0.05)
            assert (
                restore_calls == 1
            ), "A second rollback submission must wait before restore execution"
            allow_first.set()
            await first
            try:
                await second
            except ActivityRollbackUnavailable:
                pass
            else:
                raise AssertionError(
                    "A duplicate rollback submission should be rejected"
                )
        finally:
            allow_first.set()
            activity_rollback.restore_vault_file_states = original_restore

    def _preview(self, vault_name: str, activity_id: str) -> dict:
        response = self.call_api(
            f"/api/vaults/{vault_name}/activity/{activity_id}/rollback"
        )
        assert response.status_code == 200
        return response.json()

    def _execute(self, vault_name: str, activity_id: str, preview: dict):
        return self.call_api(
            f"/api/vaults/{vault_name}/activity/{activity_id}/rollback",
            method="POST",
            data={
                "expected_states": [
                    {
                        "path": item["path"],
                        "exists": item["expected_exists"],
                        "sha256": item["expected_sha256"],
                    }
                    for item in preview["paths"]
                ]
            },
        )

    def _activity_rows(self) -> list[sqlite3.Row]:
        connection = sqlite3.connect(self.run_path / "system" / "vault_state.db")
        connection.row_factory = sqlite3.Row
        try:
            return list(connection.execute("SELECT * FROM vault_activities"))
        finally:
            connection.close()
