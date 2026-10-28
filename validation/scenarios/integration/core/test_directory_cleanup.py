"""Migrated existing file behavior through real production boundaries."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.file_tools import record_operation

pytestmark = pytest.mark.scenario


async def test_empty_directory_cleanup_preserves_files(app: Application) -> None:
    from core.vault_state.file_operations import (
        delete_vault_path_operation,
        list_vault_paths_operation,
    )

    vault = app.vault("FileWriteDirectoryDeleteVault")
    app.write(vault, "cleanup/mixed/keep.md", "keep\n")
    app.write(vault, "cleanup/mixed/nonempty-child/keep.txt", "keep\n")
    app.write(vault, "cleanup/hidden-only/.keep", "hidden\n")
    app.write(vault, "cleanup/png-only/image.png", "png\n")
    (vault / "cleanup" / "empty-a" / "empty-b").mkdir(parents=True)
    (vault / "cleanup" / "mixed" / "empty-child").mkdir(parents=True)

    await app.start()

    discovery = record_operation(
        app,
        list_vault_paths_operation(
            path="cleanup", vault_path=str(vault), recursive=True, max_results=200
        ),
    )
    discovery_metadata = discovery.metadata
    assert set(discovery_metadata.get("empty_directory_candidates") or []) == {
        "cleanup/empty-a",
        "cleanup/mixed/empty-child",
    }, "Recursive list metadata should identify top-level empty directory cleanup candidates"
    assert "cleanup/mixed/nonempty-child" not in set(
        discovery_metadata.get("empty_directory_candidates") or []
    ), "Directory with descendant files should not be reported as an empty cleanup candidate"
    assert "cleanup/png-only" not in set(
        discovery_metadata.get("empty_directory_candidates") or []
    ), "Directory with only non-markdown files should not be reported as an empty cleanup candidate"
    assert "cleanup/hidden-only" not in set(
        discovery_metadata.get("empty_directory_candidates") or []
    ), "Directory with only hidden files should not be reported as an empty cleanup candidate"

    truncated_discovery = record_operation(
        app,
        list_vault_paths_operation(
            path="cleanup", vault_path=str(vault), recursive=True, max_results=2
        ),
    )
    truncated_metadata = truncated_discovery.metadata
    assert (
        truncated_metadata.get("truncated") is True
    ), "Recursive list should report truncation when capped"
    assert truncated_metadata.get("directories") == [
        "cleanup/empty-a",
        "cleanup/empty-a/empty-b",
    ], "Truncated list should preserve sorted directory-first selection"
    assert (
        truncated_metadata.get("files") == []
    ), "Directory-first truncation should omit files when directories fill the cap"

    first = record_operation(
        app,
        delete_vault_path_operation(
            vault_path=str(vault), path="cleanup", confirm_path="cleanup"
        ),
    )
    first_metadata = first.metadata
    assert (
        first_metadata.get("status") == "partial"
    ), "First directory cleanup should be partial"
    assert (
        first_metadata.get("target_type") == "directory"
    ), "Directory cleanup should identify target type"
    assert not (
        vault / "cleanup" / "empty-a"
    ).exists(), "Empty nested branch should be removed recursively"
    assert not (
        vault / "cleanup" / "mixed" / "empty-child"
    ).exists(), (
        "Empty sibling directory should be removed even when other branches remain"
    )
    assert (
        vault / "cleanup" / "mixed" / "keep.md"
    ).exists(), "File inside non-empty directory should remain"
    assert (
        vault / "cleanup" / "png-only" / "image.png"
    ).exists(), "Non-markdown file inside non-empty directory should remain"
    assert (
        vault / "cleanup" / "hidden-only" / ".keep"
    ).exists(), "Hidden file inside non-empty directory should remain"
    skipped = set(first_metadata.get("skipped_non_empty_directories") or [])
    assert {
        "cleanup",
        "cleanup/hidden-only",
        "cleanup/mixed",
        "cleanup/mixed/nonempty-child",
        "cleanup/png-only",
    }.issubset(
        skipped
    ), "Partial cleanup should report non-empty directories for follow-up"
    remaining_contents = set(first_metadata.get("remaining_directory_contents") or [])
    assert {
        "cleanup/hidden-only/.keep",
        "cleanup/mixed/keep.md",
        "cleanup/mixed/nonempty-child/keep.txt",
        "cleanup/png-only/image.png",
    }.issubset(
        remaining_contents
    ), "Partial cleanup should report remaining files, including hidden files"
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

    second = record_operation(
        app,
        delete_vault_path_operation(
            vault_path=str(vault), path="cleanup", confirm_path="cleanup"
        ),
    )
    second_metadata = second.metadata
    assert (
        second_metadata.get("status") == "completed"
    ), "Second cleanup should remove the now-empty directory tree"
    assert not (
        vault / "cleanup"
    ).exists(), "Directory root should be removed after all files are gone"
    assert (
        second_metadata.get("skipped_non_empty_directories") == []
    ), "Completed cleanup should not report skipped directories"
