"""Real mutation outcomes; lock-overlap coverage remains an explicit migration gap."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.file_boundaries import (
    move_directory,
    replace_concurrently,
    replace_stale_child,
)

pytestmark = pytest.mark.scenario


async def test_concurrent_same_path_edits_preserve_all_changes(
    app: Application,
) -> None:
    vault = app.vault("FileWriteSamePathConcurrencyVault")
    app.write(vault, "notes/concurrent.md", "alpha\nbeta\ngamma\n")
    await app.start()

    results = await replace_concurrently(
        app,
        vault,
        path="notes/concurrent.md",
        replacements=[("alpha", "one"), ("beta", "two"), ("gamma", "three")],
    )
    assert all(result.metadata["status"] == "completed" for result in results)
    assert (vault / "notes/concurrent.md").read_text(
        encoding="utf-8"
    ) == "one\ntwo\nthree\n"
    # Final content establishes preservation, not the original max-active-writes claim.


async def test_directory_move_rejects_stale_child_path(app: Application) -> None:
    from core.vault_state.file_mutations import VaultMutationRejected

    vault = app.vault("VaultDirectoryMutationConcurrencyVault")
    app.write(vault, "source/child.md", "before\n")
    await app.start()

    move_directory(app, vault, path="source", destination="destination")
    error = replace_stale_child(app, vault, path="source/child.md", content="after\n")
    assert error is not None, "The stale child-file mutation should be rejected"
    if isinstance(error, VaultMutationRejected):
        assert error.code == "file_not_found"
    assert not (vault / "source").exists()
    assert (vault / "destination/child.md").read_text(encoding="utf-8") == "before\n"
    # This sequential case does not establish waiting behind an active directory move.
