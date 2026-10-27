"""Integration scenario for vault-state manifest refresh."""

import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.vault_state.identity import resolve_or_create_vault_identity
from validation.support.base_scenario import BaseScenario


class VaultStateManifestScenario(BaseScenario):
    """Validate vault-state identity, exclusions, manifest rows, and change feed."""

    async def test_scenario(self):
        vault = self.create_vault("VaultStateManifestVault")
        self.create_file(vault, "notes/alpha.md", "Alpha v1\n")
        self.create_file(vault, "notes/beta.md", "Beta v1\n")
        self.create_file(
            vault, "AssistantMD/Chat_Sessions/export.md", "Derived transcript\n"
        )

        await self.start_system()

        from core.vault_state import VaultStateService

        service = VaultStateService()

        concurrent_vault = vault.parent / "VaultStateConcurrentIdentityVault"
        concurrent_vault.mkdir()
        with ThreadPoolExecutor(max_workers=8) as executor:
            identities = list(
                executor.map(
                    lambda _: resolve_or_create_vault_identity(
                        concurrent_vault
                    ).vault_id,
                    range(16),
                )
            )
        self.soft_assert_equal(
            len(set(identities)),
            1,
            "Concurrent vault identity creation should return one stable id",
        )

        checkpoint = self.event_checkpoint()
        first = service.refresh_vault(vault)
        first_events = self.events_since(checkpoint)

        self.soft_assert(
            (vault / "AssistantMD" / "vault.yaml").exists(),
            "vault.yaml should be created",
        )
        self.soft_assert(
            first.files_seen >= 3, "Initial refresh should see included files"
        )
        self.soft_assert_equal(
            first.files_excluded, 1, "Initial refresh should count excluded files"
        )
        self.assert_event_contains(
            first_events,
            name="vault_state_refresh_completed",
            expected={
                "vault_id": first.vault_id,
                "vault_name": vault.name,
                "files_excluded": 1,
            },
        )

        rows = self._manifest_rows()
        first_rows = {
            row["path"]: row for row in rows if row["vault_id"] == first.vault_id
        }
        self.soft_assert_equal(
            {"AssistantMD/vault.yaml", "notes/alpha.md", "notes/beta.md"}.issubset(
                set(first_rows)
            ),
            True,
            "Manifest should include user files and vault metadata",
        )
        self.soft_assert(
            "AssistantMD/Chat_Sessions/export.md" not in first_rows,
            "Excluded transcript export should not appear in manifest",
        )
        self.soft_assert_equal(
            first_rows["notes/alpha.md"]["artifact_class"],
            "user_content",
            "User note should be classified as user_content",
        )
        self.soft_assert_equal(
            first_rows["AssistantMD/vault.yaml"]["artifact_class"],
            "assistant_generated",
            "Vault metadata should be classified as assistant_generated",
        )

        alpha = vault / "notes" / "alpha.md"
        beta = vault / "notes" / "beta.md"
        alpha.write_text("Alpha v2\n", encoding="utf-8")
        beta.unlink()
        self.create_file(vault, "notes/gamma.md", "Gamma v1\n")

        renamed_vault = vault.parent / "VaultStateRenamedVault"
        vault.rename(renamed_vault)

        checkpoint = self.event_checkpoint()
        second = service.refresh_vault(renamed_vault)
        second_events = self.events_since(checkpoint)

        self.soft_assert_equal(
            second.vault_id,
            first.vault_id,
            "Renamed vault folder should keep the same vault_id",
        )
        self.soft_assert_equal(
            second.vault_name, renamed_vault.name, "Current alias should update"
        )
        self.soft_assert_equal(
            second.files_changed, 1, "Second refresh should detect one changed file"
        )
        self.soft_assert_equal(
            second.files_created, 1, "Second refresh should detect one new file"
        )
        self.soft_assert_equal(
            second.files_deleted, 1, "Second refresh should detect one deleted file"
        )
        self.assert_event_contains(
            second_events,
            name="vault_state_refresh_completed",
            expected={
                "vault_id": first.vault_id,
                "vault_name": renamed_vault.name,
                "files_changed": 1,
                "files_created": 1,
                "files_deleted": 1,
            },
        )

        second_rows = {
            row["path"]: row
            for row in self._manifest_rows()
            if row["vault_id"] == first.vault_id
        }
        self.soft_assert_equal(
            second_rows["notes/beta.md"]["deleted_at"] is not None,
            True,
            "Deleted file should remain as a soft-deleted manifest row",
        )
        self.soft_assert_equal(
            second_rows["notes/alpha.md"]["vault_name"],
            renamed_vault.name,
            "Manifest alias should update after rename",
        )
        self.soft_assert(
            "AssistantMD/Chat_Sessions/export.md" not in second_rows,
            "Excluded transcript export should not appear after rename",
        )

        changes = service.changes_since(0, vault_id=first.vault_id)
        sequences = [event.sequence for event in changes]
        self.soft_assert_equal(
            sequences,
            sorted(sequences),
            "Change-feed sequences should be monotonic",
        )
        event_pairs = [(event.path, event.event_type) for event in changes]
        self.soft_assert(
            ("notes/alpha.md", "changed") in event_pairs,
            "Change feed should expose the changed note",
        )
        self.soft_assert(
            ("notes/gamma.md", "created") in event_pairs,
            "Change feed should expose the new note",
        )
        self.soft_assert(
            ("notes/beta.md", "deleted") in event_pairs,
            "Change feed should expose the deleted note",
        )
        self.soft_assert_equal(
            {event.vault_name for event in changes[-3:]},
            {renamed_vault.name},
            "Events after rename should use the current alias",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _manifest_rows(self) -> list[dict]:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("""
                SELECT vault_id, vault_name, path, artifact_class, content_hash,
                       change_sequence, deleted_at
                FROM vault_files
                ORDER BY path
                """).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
