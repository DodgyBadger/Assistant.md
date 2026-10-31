"""Integration scenario for cleanup of empty vault directories."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.vault_state.file_operations import (
    delete_vault_path_operation,
    list_vault_paths_operation,
)
from validation.support.base_scenario import BaseScenario


class FileWriteDirectoryDeleteScenario(BaseScenario):
    """Validate directory delete removes only empty dirs and reports leftovers."""

    async def test_scenario(self):
        vault = self.create_vault("FileWriteDirectoryDeleteVault")
        self.create_file(vault, "cleanup/mixed/keep.md", "keep\n")
        self.create_file(vault, "cleanup/mixed/nonempty-child/keep.txt", "keep\n")
        self.create_file(vault, "cleanup/hidden-only/.keep", "hidden\n")
        self.create_file(vault, "cleanup/png-only/image.png", "png\n")
        (vault / "cleanup" / "empty-a" / "empty-b").mkdir(parents=True)
        (vault / "cleanup" / "mixed" / "empty-child").mkdir(parents=True)

        await self.start_system()

        discovery = list_vault_paths_operation(
            path="cleanup",
            vault_path=str(vault),
            recursive=True,
            max_results=200,
        )
        discovery_metadata = discovery.metadata
        self.soft_assert_equal(
            set(discovery_metadata.get("empty_directory_candidates") or []),
            {"cleanup/empty-a", "cleanup/mixed/empty-child"},
            "Recursive list metadata should identify top-level empty directory cleanup candidates",
        )
        self.soft_assert(
            "cleanup/mixed/nonempty-child"
            not in set(discovery_metadata.get("empty_directory_candidates") or []),
            "Directory with descendant files should not be reported as an empty cleanup candidate",
        )
        self.soft_assert(
            "cleanup/png-only"
            not in set(discovery_metadata.get("empty_directory_candidates") or []),
            "Directory with only non-markdown files should not be reported as an empty cleanup candidate",
        )
        self.soft_assert(
            "cleanup/hidden-only"
            not in set(discovery_metadata.get("empty_directory_candidates") or []),
            "Directory with only hidden files should not be reported as an empty cleanup candidate",
        )
        truncated_discovery = list_vault_paths_operation(
            path="cleanup",
            vault_path=str(vault),
            recursive=True,
            max_results=2,
        )
        truncated_metadata = truncated_discovery.metadata
        self.soft_assert_equal(
            truncated_metadata.get("truncated"),
            True,
            "Recursive list should report truncation when capped",
        )
        self.soft_assert_equal(
            truncated_metadata.get("directories"),
            ["cleanup/empty-a", "cleanup/empty-a/empty-b"],
            "Truncated list should preserve sorted directory-first selection",
        )
        self.soft_assert_equal(
            truncated_metadata.get("files"),
            [],
            "Directory-first truncation should omit files when directories fill the cap",
        )

        first = delete_vault_path_operation(
            vault_path=str(vault),
            path="cleanup",
            confirm_path="cleanup",
        )
        first_metadata = first.metadata
        self.soft_assert_equal(
            first_metadata.get("status"),
            "partial",
            "First directory cleanup should be partial",
        )
        self.soft_assert_equal(
            first_metadata.get("target_type"),
            "directory",
            "Directory cleanup should identify target type",
        )
        self.soft_assert(
            not (vault / "cleanup" / "empty-a").exists(),
            "Empty nested branch should be removed recursively",
        )
        self.soft_assert(
            not (vault / "cleanup" / "mixed" / "empty-child").exists(),
            "Empty sibling directory should be removed even when other branches remain",
        )
        self.soft_assert(
            (vault / "cleanup" / "mixed" / "keep.md").exists(),
            "File inside non-empty directory should remain",
        )
        self.soft_assert(
            (vault / "cleanup" / "png-only" / "image.png").exists(),
            "Non-markdown file inside non-empty directory should remain",
        )
        self.soft_assert(
            (vault / "cleanup" / "hidden-only" / ".keep").exists(),
            "Hidden file inside non-empty directory should remain",
        )
        skipped = set(first_metadata.get("skipped_non_empty_directories") or [])
        self.soft_assert(
            {
                "cleanup",
                "cleanup/hidden-only",
                "cleanup/mixed",
                "cleanup/mixed/nonempty-child",
                "cleanup/png-only",
            }.issubset(skipped),
            "Partial cleanup should report non-empty directories for follow-up",
        )
        remaining_contents = set(
            first_metadata.get("remaining_directory_contents") or []
        )
        self.soft_assert(
            {
                "cleanup/hidden-only/.keep",
                "cleanup/mixed/keep.md",
                "cleanup/mixed/nonempty-child/keep.txt",
                "cleanup/png-only/image.png",
            }.issubset(remaining_contents),
            "Partial cleanup should report remaining files, including hidden files",
        )

        delete_vault_path_operation(
            vault_path=str(vault),
            path="cleanup/mixed/keep.md",
            confirm_path="cleanup/mixed/keep.md",
        )
        delete_vault_path_operation(
            vault_path=str(vault),
            path="cleanup/mixed/nonempty-child/keep.txt",
            confirm_path="cleanup/mixed/nonempty-child/keep.txt",
        )
        delete_vault_path_operation(
            vault_path=str(vault),
            path="cleanup/png-only/image.png",
            confirm_path="cleanup/png-only/image.png",
        )
        delete_vault_path_operation(
            vault_path=str(vault),
            path="cleanup/hidden-only/.keep",
            confirm_path="cleanup/hidden-only/.keep",
        )
        second = delete_vault_path_operation(
            vault_path=str(vault),
            path="cleanup",
            confirm_path="cleanup",
        )
        second_metadata = second.metadata
        self.soft_assert_equal(
            second_metadata.get("status"),
            "completed",
            "Second cleanup should remove the now-empty directory tree",
        )
        self.soft_assert(
            not (vault / "cleanup").exists(),
            "Directory root should be removed after all files are gone",
        )
        self.soft_assert_equal(
            second_metadata.get("skipped_non_empty_directories"),
            [],
            "Completed cleanup should not report skipped directories",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
