"""Root deletion rejects before touching any file or empty directory."""

from __future__ import annotations

import pytest

from validation.support.application import Application
from validation.support.vault.file_tools import BoundFileTools, vault_tree_snapshot
from validation.support.vault.vault_state import wait_for_event

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("consumer", ["tool", "explorer"])
@pytest.mark.parametrize("alias", [".", "./", "././"])
@pytest.mark.parametrize("contents", ["populated", "empty_directories", "empty"])
async def test_root_alias_rejection_preserves_complete_vault(
    app: Application, consumer: str, alias: str, contents: str
) -> None:
    # Finish the real background rescan before creating the target: metadata
    # initialization must not race the exact before/after preservation oracle.
    checkpoint = app.events.checkpoint()
    await app.start()
    await wait_for_event(
        app,
        checkpoint,
        name="vault_state_background_refresh_completed",
        expected={"reason": "startup"},
    )
    # Create after startup so even the empty case has no seeded product files.
    vault = app.vault("RootProtectedVault")
    if contents == "empty":
        for directory in sorted(vault.rglob("*"), reverse=True):
            assert directory.is_dir()
            directory.rmdir()
    else:
        (vault / "Empty/Nested").mkdir(parents=True)
    if contents == "populated":
        app.write(vault, "Keep.md", "retain this file\n")
        app.write(vault, "Notes/keep.md", "retain this nested file\n")
        (vault / "Notes/bytes.bin").write_bytes(b"\x00\xffbinary\n")
    before = vault_tree_snapshot(vault)
    app.evidence.save_json("vault-before.json", before)
    if consumer == "tool":
        result = await BoundFileTools(app, vault).call(
            "file_write", operation="delete", path=alias
        )
        rejected = result.metadata.get("error_type") == "invalid_target"
    else:
        sources = ["Keep.md", alias] if contents == "populated" else [alias]
        response = app.request(
            f"/api/vaults/{vault.name}/paths/delete-batch",
            "POST",
            {"sources": sources, "confirmed_sources": sources},
        )
        rejected = (
            response.status_code == 400 and response.json()["error"] == "invalid_target"
        )
    after = vault_tree_snapshot(vault)
    app.evidence.save_json("vault-after.json", after)
    assert (
        after == before
    ), "Rejected root deletion must preserve exact bytes and empty directories"
    assert rejected, "A vault-root target must be rejected as invalid_target"
