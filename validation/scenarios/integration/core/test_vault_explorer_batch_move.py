"""Atomic Explorer move preflight through the real API."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_explorer_batch_move_groups_activity_and_rejects_preflight_conflicts(
    app: Application,
) -> None:
    vault = app.vault("VaultExplorerBatchMoveVault")
    app.write(vault, "One/a.md", "a")
    app.write(vault, "Two/b.md", "b")
    (vault / "Archive").mkdir()
    await app.start()
    endpoint = f"/api/vaults/{vault.name}/paths/move-batch"
    moved = app.request(
        endpoint,
        "POST",
        {"sources": ["One/a.md", "Two/b.md"], "destination": "Archive"},
    )
    assert moved.status_code == 200, moved.text
    assert (vault / "Archive/a.md").read_text() == "a"
    assert (vault / "Archive/b.md").read_text() == "b"
    assert not (vault / "One/a.md").exists()
    assert not (vault / "Two/b.md").exists()
    groups = app.request(f"/api/vaults/{vault.name}/activity").json()["groups"]
    assert len([g for g in groups if g.get("activity_label") == "Move 2 items"]) == 1
    app.write(vault, "Three/a.md", "collision")
    collision = app.request(
        endpoint,
        "POST",
        {"sources": ["Archive/a.md", "Archive/b.md"], "destination": "Three"},
    )
    assert collision.status_code == 409
    assert (vault / "Archive/a.md").read_text() == "a"
    assert (vault / "Archive/b.md").read_text() == "b"
    assert (vault / "Three/a.md").read_text() == "collision"
    assert not (vault / "Three/b.md").exists()
    app.write(vault, "Tree/child.md", "child")
    (vault / "Alias").symlink_to(vault / "Tree", target_is_directory=True)
    (vault / "AliasTarget").mkdir()
    overlap = app.request(
        endpoint,
        "POST",
        {"sources": ["Alias", "Tree/child.md"], "destination": "AliasTarget"},
    )
    assert overlap.status_code == 400
    assert (vault / "Tree/child.md").read_text() == "child"
    assert not any((vault / "AliasTarget").iterdir())
