"""Validate atomic multi-item moves through the Vault Explorer API."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from api.services import vault_files
from validation.core.base_scenario import BaseScenario


class VaultExplorerBatchMoveScenario(BaseScenario):
    """Prove preflight, one activity, and compensation for batch Move."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("VaultExplorerBatchMoveVault")
        (vault / "One").mkdir()
        (vault / "One/a.md").write_text("a", encoding="utf-8")
        (vault / "Two").mkdir()
        (vault / "Two/b.md").write_text("b", encoding="utf-8")
        (vault / "Archive").mkdir()
        await self.start_system()

        moved = self.call_api(
            f"/api/vaults/{vault.name}/paths/move-batch",
            method="POST",
            data={
                "sources": ["One/a.md", "Two/b.md"],
                "destination": "Archive",
            },
        )
        self.soft_assert_equal(moved.status_code, 200, "Batch Move should succeed")
        self.soft_assert(
            (vault / "Archive/a.md").exists() and (vault / "Archive/b.md").exists(),
            "Every selected source should move to the chosen folder",
        )
        activity = self.call_api(f"/api/vaults/{vault.name}/activity").json()
        matching = [
            group
            for group in activity.get("groups", [])
            if group.get("activity_label") == "Move 2 items"
        ]
        self.soft_assert_equal(
            len(matching), 1, "One batch command should create one Explorer activity"
        )

        (vault / "Three").mkdir()
        (vault / "Three/a.md").write_text("collision", encoding="utf-8")
        collision = self.call_api(
            f"/api/vaults/{vault.name}/paths/move-batch",
            method="POST",
            data={
                "sources": ["Archive/a.md", "Archive/b.md"],
                "destination": "Three",
            },
        )
        self.soft_assert_equal(
            collision.status_code, 409, "A target collision should fail preflight"
        )
        self.soft_assert(
            (vault / "Archive/a.md").exists() and (vault / "Archive/b.md").exists(),
            "Failed preflight should leave every source unchanged",
        )

        (vault / "Restore").mkdir()
        original_mutate = vault_files._mutate_vault_path_attributed  # noqa: SLF001

        def fail_second_source(**kwargs):
            if kwargs.get("normalized") == "Archive/b.md":
                raise RuntimeError("injected batch move failure")
            return original_mutate(**kwargs)

        with patch.object(
            vault_files,
            "_mutate_vault_path_attributed",
            side_effect=fail_second_source,
        ):
            compensated = self.call_api(
                f"/api/vaults/{vault.name}/paths/move-batch",
                method="POST",
                data={
                    "sources": ["Archive/a.md", "Archive/b.md"],
                    "destination": "Restore",
                },
            )
        self.soft_assert_equal(
            compensated.status_code,
            500,
            "An injected mid-batch failure should fail the command",
        )
        self.soft_assert(
            (vault / "Archive/a.md").exists()
            and (vault / "Archive/b.md").exists()
            and not (vault / "Restore/a.md").exists(),
            "A mid-batch failure should compensate completed moves",
        )
