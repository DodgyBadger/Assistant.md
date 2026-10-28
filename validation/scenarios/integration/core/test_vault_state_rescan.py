"""Existing vault behavior through real application boundaries."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_startup_manual_and_restart_refresh(app: Application) -> None:
    from validation.scenarios.support.vault_state import (
        manifest_row_exists,
        restart_application,
        wait_for_event,
    )

    vault = app.vault("VaultStateStartupVault")
    app.write(vault, "notes/startup.md", "Startup v1\n")

    checkpoint = app.events.checkpoint()
    await app.start()
    await wait_for_event(
        app,
        checkpoint,
        name="vault_state_refresh_all_completed",
        expected={"vaults_refreshed": 1, "vaults_failed": 0},
    )
    completed = app.events.require(
        after=checkpoint,
        name="vault_state_refresh_completed",
        fields={"vault_name": vault.name, "files_excluded": 0},
    )
    vault_id = completed["data"]["vault_id"]
    app.events.require(
        after=checkpoint,
        name="vault_state_background_refresh_completed",
        fields={"reason": "startup", "vault_state_refreshed": 1},
    )
    assert manifest_row_exists(
        app, vault_id, "notes/startup.md", deleted=False
    ), "Startup refresh should record existing vault files"

    (vault / "notes" / "startup.md").write_text("Startup v2\n", encoding="utf-8")
    app.write(vault, "notes/manual.md", "Manual rescan\n")

    checkpoint = app.events.checkpoint()
    response = app.request("/api/vaults/rescan", method="POST")
    assert response.status_code == 200, "Manual vault rescan should succeed"
    app.events.require(
        after=checkpoint,
        name="vault_state_refresh_completed",
        fields={
            "vault_id": vault_id,
            "vault_name": vault.name,
            "files_changed": 1,
            "files_created": 1,
        },
    )
    assert manifest_row_exists(
        app, vault_id, "notes/manual.md", deleted=False
    ), "Manual rescan should record newly added vault files"

    (vault / "notes" / "manual.md").unlink()

    checkpoint = app.events.checkpoint()
    await restart_application(app)
    await wait_for_event(
        app,
        checkpoint,
        name="vault_state_refresh_completed",
        expected={"vault_id": vault_id, "vault_name": vault.name, "files_deleted": 1},
    )
    app.events.require(
        after=checkpoint,
        name="vault_state_refresh_completed",
        fields={"vault_id": vault_id, "vault_name": vault.name, "files_deleted": 1},
    )
    assert manifest_row_exists(
        app, vault_id, "notes/manual.md", deleted=True
    ), "Restart refresh should soft-delete files removed while stopped"
