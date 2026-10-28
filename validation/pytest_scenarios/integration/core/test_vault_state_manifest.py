"""Existing vault behavior through real application boundaries."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_manifest_tracks_identity_exclusions_and_changes(
    app: Application,
) -> None:
    from core.vault_state import VaultStateService
    from validation.pytest_scenarios.support.vault_state import (
        changes_since,
        concurrent_identities,
        manifest_rows,
        refresh_vault,
    )

    vault = app.vault("VaultStateManifestVault")
    app.write(vault, "notes/alpha.md", "Alpha v1\n")
    app.write(vault, "notes/beta.md", "Beta v1\n")
    app.write(vault, "AssistantMD/Chat_Sessions/export.md", "Derived transcript\n")
    await app.start()
    service = VaultStateService()
    identities = concurrent_identities(
        app, vault.parent / "VaultStateConcurrentIdentityVault"
    )
    assert (
        len(set(identities)) == 1
    ), "Concurrent vault identity creation should return one stable id"

    checkpoint = app.events.checkpoint()
    first = refresh_vault(app, service, vault)
    assert (
        vault / "AssistantMD" / "vault.yaml"
    ).exists(), "vault.yaml should be created"
    assert first.files_seen >= 3, "Initial refresh should see included files"
    assert first.files_excluded == 1, "Initial refresh should count excluded files"
    app.events.require(
        after=checkpoint,
        name="vault_state_refresh_completed",
        fields={
            "vault_id": first.vault_id,
            "vault_name": vault.name,
            "files_excluded": 1,
        },
    )

    rows = manifest_rows(app)
    first_rows = {row["path"]: row for row in rows if row["vault_id"] == first.vault_id}
    assert {"AssistantMD/vault.yaml", "notes/alpha.md", "notes/beta.md"}.issubset(
        set(first_rows)
    ), "Manifest should include user files and vault metadata"
    assert (
        "AssistantMD/Chat_Sessions/export.md" not in first_rows
    ), "Excluded transcript export should not appear in manifest"
    assert (
        first_rows["notes/alpha.md"]["artifact_class"] == "user_content"
    ), "User note should be classified as user_content"
    assert (
        first_rows["AssistantMD/vault.yaml"]["artifact_class"] == "assistant_generated"
    ), "Vault metadata should be classified as assistant_generated"

    alpha = vault / "notes" / "alpha.md"
    beta = vault / "notes" / "beta.md"
    alpha.write_text("Alpha v2\n", encoding="utf-8")
    beta.unlink()
    app.write(vault, "notes/gamma.md", "Gamma v1\n")

    renamed_vault = vault.parent / "VaultStateRenamedVault"
    vault.rename(renamed_vault)

    checkpoint = app.events.checkpoint()
    second = refresh_vault(app, service, renamed_vault)
    assert (
        second.vault_id == first.vault_id
    ), "Renamed vault folder should keep the same vault_id"
    assert second.vault_name == renamed_vault.name, "Current alias should update"
    assert second.files_changed == 1, "Second refresh should detect one changed file"
    assert second.files_created == 1, "Second refresh should detect one new file"
    assert second.files_deleted == 1, "Second refresh should detect one deleted file"
    app.events.require(
        after=checkpoint,
        name="vault_state_refresh_completed",
        fields={
            "vault_id": first.vault_id,
            "vault_name": renamed_vault.name,
            "files_changed": 1,
            "files_created": 1,
            "files_deleted": 1,
        },
    )

    second_rows = {
        row["path"]: row
        for row in manifest_rows(app)
        if row["vault_id"] == first.vault_id
    }
    assert (
        second_rows["notes/beta.md"]["deleted_at"] is not None
    ), "Deleted file should remain as a soft-deleted manifest row"
    assert (
        second_rows["notes/alpha.md"]["vault_name"] == renamed_vault.name
    ), "Manifest alias should update after rename"
    assert (
        "AssistantMD/Chat_Sessions/export.md" not in second_rows
    ), "Excluded transcript export should not appear after rename"

    changes = changes_since(app, service, first.vault_id)
    sequences = [event.sequence for event in changes]
    assert sequences == sorted(sequences), "Change-feed sequences should be monotonic"
    event_pairs = [(event.path, event.event_type) for event in changes]
    assert (
        "notes/alpha.md",
        "changed",
    ) in event_pairs, "Change feed should expose the changed note"
    assert (
        "notes/gamma.md",
        "created",
    ) in event_pairs, "Change feed should expose the new note"
    assert (
        "notes/beta.md",
        "deleted",
    ) in event_pairs, "Change feed should expose the deleted note"
    assert {event.vault_name for event in changes[-3:]} == {
        renamed_vault.name
    }, "Events after rename should use the current alias"
