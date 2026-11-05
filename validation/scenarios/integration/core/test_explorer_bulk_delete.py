"""Explorer deletion retains recoverable file states and complete directory topology."""

import pytest

from validation.support.application import Application
from validation.support.vault.vault_recovery import execute_rollback, preview_rollback

pytestmark = pytest.mark.scenario


async def test_recursive_bulk_delete_and_activity_rollback_restore_exact_states(
    app: Application,
):
    vault = app.vault("DeleteVault")
    app.write(vault, "Tree/Nested/A.md", "original text")
    app.write(vault, "Elsewhere/B.bin", "")
    (vault / "Elsewhere/B.bin").write_bytes(b"\x00\xffbinary\n")
    app.write(vault, "Keep.md", "untouched")
    (vault / "Tree/Empty/Deep").mkdir(parents=True)
    await app.start()
    sources = ["Tree", "Elsewhere/B.bin", "Tree/Nested/A.md"]
    response = app.request(
        f"/api/vaults/{vault.name}/paths/delete-batch",
        "POST",
        {"sources": sources, "confirmed_sources": sources},
    )
    assert response.status_code == 200, response.text
    activity_id = response.json()["activity_id"]
    assert not (vault / "Tree").exists()
    assert not (vault / "Elsewhere/B.bin").exists()
    assert (vault / "Keep.md").read_text() == "untouched"
    preview = preview_rollback(app, vault.name, activity_id)
    assert preview["can_rollback"] is True, preview
    rollback = execute_rollback(app, vault.name, activity_id, preview)
    assert rollback.status_code == 200, rollback.text
    assert (vault / "Tree/Nested/A.md").read_text() == "original text"
    assert (vault / "Elsewhere/B.bin").read_bytes() == b"\x00\xffbinary\n"
    assert (vault / "Tree/Empty/Deep").is_dir()
    undo_id = rollback.json()["rollback_activity_id"]
    app.write(vault, "Tree/New.md", "newer content")
    blocked = preview_rollback(app, vault.name, undo_id)
    assert blocked["can_rollback"] is False
    rejected = execute_rollback(app, vault.name, undo_id, blocked)
    assert rejected.status_code == 409
    assert (vault / "Tree/New.md").read_text() == "newer content"
    assert (vault / "Elsewhere/B.bin").exists()
    (vault / "Tree/New.md").unlink()
    undo_preview = preview_rollback(app, vault.name, undo_id)
    assert undo_preview["can_rollback"] is True, undo_preview
    undo = execute_rollback(app, vault.name, undo_id, undo_preview)
    assert undo.status_code == 200, undo.text
    assert not (vault / "Tree").exists()
    assert not (vault / "Elsewhere/B.bin").exists()


async def test_bulk_delete_preflights_confirmation_and_all_targets(app: Application):
    vault = app.vault("DeletePreflightVault")
    app.write(vault, "Keep.md", "retain me")
    app.write(vault, "Tree/Keep.md", "also retain")
    await app.start()
    url = f"/api/vaults/{vault.name}/paths/delete-batch"
    for sources, confirmed in (
        (["Keep.md"], []),
        (["Keep.md", "Missing.md"], ["Keep.md", "Missing.md"]),
        (["Keep.md", "../outside"], ["Keep.md", "../outside"]),
        (["Keep.md", "."], ["Keep.md", "."]),
    ):
        result = app.request(
            url, "POST", {"sources": sources, "confirmed_sources": confirmed}
        )
        assert result.status_code in {400, 404, 409, 422}, result.text
        assert (vault / "Keep.md").read_text() == "retain me"
        assert (vault / "Tree/Keep.md").read_text() == "also retain"
    missing_single = app.request(
        f"/api/vaults/{vault.name}/paths/mutate",
        "POST",
        {"operation": "delete", "path": "Missing.md"},
    )
    assert missing_single.status_code == 404
    (vault / "Tree/Link.md").symlink_to(vault / "Keep.md")
    result = app.request(
        url, "POST", {"sources": ["Tree"], "confirmed_sources": ["Tree"]}
    )
    assert result.status_code in {400, 409}, result.text
    assert (vault / "Tree/Keep.md").read_text() == "also retain"
    assert (vault / "Keep.md").read_text() == "retain me"
