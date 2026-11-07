"""Migrated existing file behavior through real production boundaries."""

from __future__ import annotations

import pytest

from validation.support.application import Application
from validation.support.vault.file_tools import record_operation

pytestmark = pytest.mark.scenario


async def test_directory_deletion_and_recovery_preserve_exact_contents(
    app: Application,
) -> None:
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

    # The accepted file-tool contract now deletes selected folders recursively.
    from validation.support.vault.file_tools import vault_tree_snapshot
    from validation.support.vault.vault_recovery import (
        execute_rollback,
        preview_rollback,
    )

    before = vault_tree_snapshot(vault / "cleanup")
    deleted = record_operation(
        app,
        delete_vault_path_operation(vault_path=str(vault), path="cleanup"),
    )
    assert deleted.metadata["status"] == "completed"
    assert deleted.metadata["target_type"] == "directory"
    assert not (vault / "cleanup").exists()
    activity_id = deleted.metadata["activity_id"]
    preview = preview_rollback(app, vault.name, activity_id)
    assert preview["can_rollback"] is True, preview
    restored = execute_rollback(app, vault.name, activity_id, preview)
    assert restored.status_code == 200, restored.text
    assert vault_tree_snapshot(vault / "cleanup") == before
