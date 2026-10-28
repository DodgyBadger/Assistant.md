"""Integration scenario for vault-state startup and manual rescan refresh."""

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.support.base_scenario import BaseScenario


class VaultStateStartupRescanScenario(BaseScenario):
    """Validate vault-state refresh is wired into startup and manual rescan."""

    async def test_scenario(self):
        vault = self.create_vault("VaultStateStartupVault")
        self.create_file(vault, "notes/startup.md", "Startup v1\n")

        checkpoint = self.event_checkpoint()
        await self.start_system()
        startup_events = await self._wait_for_event(
            checkpoint,
            name="vault_state_refresh_all_completed",
            expected={"vaults_refreshed": 1, "vaults_failed": 0},
        )

        completed = self.assert_event_contains(
            startup_events,
            name="vault_state_refresh_completed",
            expected={
                "vault_name": vault.name,
                "files_excluded": 0,
            },
        )
        vault_id = completed["data"]["vault_id"]
        self.assert_event_contains(
            startup_events,
            name="vault_state_background_refresh_completed",
            expected={"reason": "startup", "vault_state_refreshed": 1},
        )
        self.soft_assert(
            self._manifest_row_exists(vault_id, "notes/startup.md", deleted=False),
            "Startup refresh should record existing vault files",
        )

        (vault / "notes" / "startup.md").write_text("Startup v2\n", encoding="utf-8")
        self.create_file(vault, "notes/manual.md", "Manual rescan\n")

        checkpoint = self.event_checkpoint()
        response = self.call_api("/api/vaults/rescan", method="POST")
        rescan_events = self.events_since(checkpoint)

        self.soft_assert_equal(
            response.status_code, 200, "Manual vault rescan should succeed"
        )
        self.assert_event_contains(
            rescan_events,
            name="vault_state_refresh_completed",
            expected={
                "vault_id": vault_id,
                "vault_name": vault.name,
                "files_changed": 1,
                "files_created": 1,
            },
        )
        self.soft_assert(
            self._manifest_row_exists(vault_id, "notes/manual.md", deleted=False),
            "Manual rescan should record newly added vault files",
        )

        (vault / "notes" / "manual.md").unlink()

        checkpoint = self.event_checkpoint()
        await self.restart_system()
        restart_events = await self._wait_for_event(
            checkpoint,
            name="vault_state_refresh_completed",
            expected={
                "vault_id": vault_id,
                "vault_name": vault.name,
                "files_deleted": 1,
            },
        )

        self.assert_event_contains(
            restart_events,
            name="vault_state_refresh_completed",
            expected={
                "vault_id": vault_id,
                "vault_name": vault.name,
                "files_deleted": 1,
            },
        )
        self.soft_assert(
            self._manifest_row_exists(vault_id, "notes/manual.md", deleted=True),
            "Restart refresh should soft-delete files removed while stopped",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    async def _wait_for_event(
        self,
        checkpoint: int,
        *,
        name: str,
        expected: dict,
        timeout_seconds: float = 5.0,
    ) -> list[dict]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            events = self.events_since(checkpoint)
            if self.find_events(events, name=name, data=expected):
                return events
            if asyncio.get_running_loop().time() >= deadline:
                return events
            await asyncio.sleep(0.05)

    def _manifest_row_exists(self, vault_id: str, path: str, *, deleted: bool) -> bool:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT deleted_at
                FROM vault_files
                WHERE vault_id = ? AND path = ?
                """,
                (vault_id, path),
            ).fetchone()
            if row is None:
                return False
            is_deleted = row[0] is not None
            return is_deleted is deleted
        finally:
            conn.close()
