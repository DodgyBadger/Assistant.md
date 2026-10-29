"""Real concurrent edits preserve file contents; stale paths cannot redirect writes."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.file_boundaries import (
    move_directory,
    replace_concurrently,
    replace_stale_child,
)
from validation.scenarios.support.vault_recovery import query_rows

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
    mutations = query_rows(
        app,
        "SELECT m.before_hash, m.after_hash, m.status, f.content_hash, f.snapshot_ref, s.snapshot_root "
        "FROM vault_mutations m JOIN file_snapshots f ON f.id=m.before_snapshot_id "
        "JOIN snapshot_sets s ON s.id=f.snapshot_set_id "
        "WHERE m.path=? ORDER BY m.id",
        ("notes/concurrent.md",),
    )
    assert len(mutations) == 3
    previous_hash = sha256(b"alpha\nbeta\ngamma\n").hexdigest()
    for mutation in mutations:
        assert mutation["status"] == "completed"
        assert mutation["before_hash"] == mutation["content_hash"] == previous_hash
        assert (
            sha256(
                (
                    Path(mutation["snapshot_root"]) / mutation["snapshot_ref"]
                ).read_bytes()
            ).hexdigest()
            == previous_hash
        )
        previous_hash = mutation["after_hash"]
    final_hash = sha256((vault / "notes/concurrent.md").read_bytes()).hexdigest()
    assert previous_hash == final_hash
    assert query_rows(
        app,
        "SELECT content_hash FROM vault_files WHERE path=?",
        ("notes/concurrent.md",),
    ) == [{"content_hash": final_hash}]


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
