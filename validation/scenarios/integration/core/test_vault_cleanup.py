"""Real vault recovery behavior and durable outcomes."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_cleanup_removes_only_expired_safety_artifacts(app: Application) -> None:
    from datetime import UTC, datetime, timedelta

    from validation.scenarios.support.vault_recovery import (
        mutation_task_ids,
        prepare_cleanup_state,
        snapshot_task_ids,
    )

    vault = app.vault("VaultStateCleanupVault")
    app.write(vault, "notes/keep.md", "Do not delete vault content\n")
    await app.start()
    now = datetime.now(UTC)
    expired_snapshot_root, retained_file = prepare_cleanup_state(
        app,
        vault,
        expired_at=now - timedelta(days=1),
        retained_at=now + timedelta(days=1),
    )

    checkpoint = app.events.checkpoint()
    response = app.request("/api/vault-state/cleanup", method="POST")
    assert response.status_code == 200, "Cleanup endpoint should respond"
    payload = response.json()
    assert payload.get("success") is True, "Cleanup should succeed"
    assert (
        payload.get("expired_activity_rows_deleted") == 1
    ), "Cleanup should delete expired activity rows"
    assert (
        payload.get("expired_mutation_rows_deleted") == 1
    ), "Cleanup should delete expired mutation rows"
    assert (
        payload.get("expired_snapshot_rows_deleted") == 1
    ), "Cleanup should delete expired snapshot rows"
    assert (
        payload.get("snapshot_files_deleted") == 1
    ), "Cleanup should delete expired snapshot files"
    assert (
        payload.get("snapshot_dirs_deleted", 0) >= 1
    ), "Cleanup should delete expired snapshot directories"
    app.events.require(
        after=checkpoint,
        name="vault_state_cleanup_completed",
        fields={
            "expired_activity_rows_deleted": 1,
            "expired_mutation_rows_deleted": 1,
            "expired_snapshot_rows_deleted": 1,
            "snapshot_files_deleted": 1,
        },
    )

    remaining_mutations = mutation_task_ids(app)
    remaining_snapshots = snapshot_task_ids(app)
    assert (
        "expired-task" not in remaining_mutations
    ), "Expired mutation row should be removed"
    assert (
        "retained-task" in remaining_mutations
    ), "Unexpired mutation row should remain"
    assert (
        "expired-task" not in remaining_snapshots
    ), "Expired snapshot row should be removed"
    assert (
        "retained-task" in remaining_snapshots
    ), "Unexpired snapshot row should remain"
    assert (
        not expired_snapshot_root.exists()
    ), "Expired snapshot directory should be deleted"
    assert retained_file.exists(), "Unexpired snapshot file should remain"
    assert (vault / "notes" / "keep.md").exists(), "Cleanup must not delete vault files"
