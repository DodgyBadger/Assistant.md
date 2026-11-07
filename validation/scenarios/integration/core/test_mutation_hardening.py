"""Recovery rejects unavailable topology and retains unrecorded directory states."""

import sqlite3

import pytest

from validation.support.application import Application
from validation.support.vault.vault_recovery import execute_rollback, preview_rollback

pytestmark = pytest.mark.scenario


async def test_delete_preflights_vault_identity_before_touching_mixed_selection(
    app: Application,
):
    vault = app.vault("ProtectedIdentityVault")
    app.write(vault, "Keep.md", "retained content")
    await app.start()
    # A real favorites read establishes this vault's stable identity.
    assert app.request(f"/api/vaults/{vault.name}/favorites").status_code == 200
    identity = vault / "AssistantMD/vault.yaml"
    before = identity.read_bytes()
    for protected in ("AssistantMD/vault.yaml", "AssistantMD"):
        sources = ["Keep.md", protected]
        deletion = app.request(
            f"/api/vaults/{vault.name}/paths/delete-batch",
            "POST",
            {"sources": sources, "confirmed_sources": sources},
        )
        assert deletion.status_code == 400
        assert deletion.json()["error"] == "protected_vault_identity"
        assert (vault / "Keep.md").read_text() == "retained content"
        assert identity.read_bytes() == before
        activity = app.request(f"/api/vaults/{vault.name}/activity").json()
        assert not any(group["mutations"] for group in activity["groups"])


async def test_directory_rollback_preview_rejects_missing_or_blocked_parent(
    app: Application,
):
    vault = app.vault("DirectoryParentVault")
    app.write(vault, "Parent/Child/Note.md", "child content")
    await app.start()
    url = f"/api/vaults/{vault.name}/paths/delete-batch"
    first = app.request(
        url,
        "POST",
        {"sources": ["Parent/Child"], "confirmed_sources": ["Parent/Child"]},
    )
    assert first.status_code == 200, first.text
    second = app.request(
        url, "POST", {"sources": ["Parent"], "confirmed_sources": ["Parent"]}
    )
    assert second.status_code == 200, second.text
    activity_id = first.json()["activity_id"]
    for blocked in (False, True):
        if blocked:
            response = app.request(
                f"/api/vaults/{vault.name}/paths/mutate",
                "POST",
                {
                    "operation": "create_file",
                    "path": "Parent",
                    "content": "new parent file",
                },
            )
            assert response.status_code == 200, response.text
        preview = preview_rollback(app, vault.name, activity_id)
        assert preview["can_rollback"] is False
        assert any(
            issue["code"] == "parent_state_conflict" for issue in preview["issues"]
        )
        rejection = execute_rollback(app, vault.name, activity_id, preview)
        assert rejection.status_code == 409
        assert not (vault / "Parent/Child").exists()
        if blocked:
            assert (vault / "Parent").read_text() == "new parent file"


async def test_directory_journal_failure_restores_unrecorded_empty_directory(
    app: Application,
):
    vault = app.vault("DirectoryJournalFailureVault")
    app.write(vault, "Keep.md", "recoverable file bytes")
    (vault / "Empty/Deep").mkdir(parents=True)
    await app.start()
    assert app.request(f"/api/vaults/{vault.name}/favorites").status_code == 200
    # Simulate a real durable-storage write failure at the SQLite boundary; all
    # application preflight, filesystem mutation, journaling and recovery stay real.
    with sqlite3.connect(app.root / "system/vault_state.db") as connection:
        connection.executescript(
            "CREATE TRIGGER reject_directory_record BEFORE INSERT ON vault_mutations "
            "WHEN NEW.target_kind = 'directory' "
            "BEGIN SELECT RAISE(ABORT, 'injected directory journal storage failure'); END;"
        )
    try:
        deletion = app.request(
            f"/api/vaults/{vault.name}/paths/delete-batch",
            "POST",
            {
                "sources": ["Keep.md", "Empty"],
                "confirmed_sources": ["Keep.md", "Empty"],
            },
        )
        assert deletion.status_code == 500
        # The unrecorded physical rmdir must be compensated immediately.
        assert (vault / "Empty/Deep").is_dir()
    finally:
        with sqlite3.connect(app.root / "system/vault_state.db") as connection:
            connection.execute("DROP TRIGGER reject_directory_record")
    activity = app.request(f"/api/vaults/{vault.name}/activity").json()
    failed = next(group for group in activity["groups"] if group["status"] == "failed")
    assert (vault / "Keep.md").read_text() == "recoverable file bytes"
    assert (vault / "Empty/Deep").is_dir()
    # Recovery of retained file states was attempted before reporting failure.
    assert failed["rollback_status"] == "completed"
