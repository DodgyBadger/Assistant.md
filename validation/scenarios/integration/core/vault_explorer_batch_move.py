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

        (vault / "Tree").mkdir()
        (vault / "Tree/child.md").write_text("child", encoding="utf-8")
        (vault / "Alias").symlink_to(vault / "Tree", target_is_directory=True)
        (vault / "AliasTarget").mkdir()
        alias_overlap = self.call_api(
            f"/api/vaults/{vault.name}/paths/move-batch",
            method="POST",
            data={
                "sources": ["Alias", "Tree/child.md"],
                "destination": "AliasTarget",
            },
        )
        self.soft_assert_equal(
            alias_overlap.status_code,
            400,
            "Resolved symlink aliases should not disguise overlapping sources",
        )
        self.soft_assert(
            (vault / "Tree/child.md").exists()
            and not any((vault / "AliasTarget").iterdir()),
            "Rejected resolved-path overlap should leave the tree unchanged",
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

        def fail_after_second_file(**kwargs):
            result = original_mutate(**kwargs)
            if kwargs.get("normalized") == "Archive/b.md":
                raise RuntimeError("injected post-move file failure")
            return result

        with patch.object(
            vault_files,
            "_mutate_vault_path_attributed",
            side_effect=fail_after_second_file,
        ):
            post_move_file = self.call_api(
                f"/api/vaults/{vault.name}/paths/move-batch",
                method="POST",
                data={
                    "sources": ["Archive/a.md", "Archive/b.md"],
                    "destination": "Restore",
                },
            )
        self.soft_assert_equal(
            post_move_file.status_code,
            500,
            "A failure after a file move should fail the batch command",
        )
        self.soft_assert(
            (vault / "Archive/a.md").exists()
            and (vault / "Archive/b.md").exists()
            and not (vault / "Restore/a.md").exists()
            and not (vault / "Restore/b.md").exists(),
            "Compensation should reconcile a file moved before its helper failed",
        )

        (vault / "DirOne").mkdir()
        (vault / "DirOne/one.md").write_text("one", encoding="utf-8")
        (vault / "DirTwo").mkdir()
        (vault / "DirTwo/two.md").write_text("two", encoding="utf-8")
        (vault / "DirTarget").mkdir()

        def fail_after_second_directory(**kwargs):
            result = original_mutate(**kwargs)
            if kwargs.get("normalized") == "DirTwo":
                raise RuntimeError("injected post-move directory failure")
            return result

        with patch.object(
            vault_files,
            "_mutate_vault_path_attributed",
            side_effect=fail_after_second_directory,
        ):
            post_move_directory = self.call_api(
                f"/api/vaults/{vault.name}/paths/move-batch",
                method="POST",
                data={
                    "sources": ["DirOne", "DirTwo"],
                    "destination": "DirTarget",
                },
            )
        self.soft_assert_equal(
            post_move_directory.status_code,
            500,
            "A failure after a directory move should fail the batch command",
        )
        self.soft_assert(
            (vault / "DirOne/one.md").exists()
            and (vault / "DirTwo/two.md").exists()
            and not (vault / "DirTarget/DirOne").exists()
            and not (vault / "DirTarget/DirTwo").exists(),
            "Compensation should reconcile a directory moved before its helper failed",
        )
