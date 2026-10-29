"""Contending directory edits and rollbacks preserve coherent durable state."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.vault_mutation_contention import (
    move_with_competing_edit,
)
from validation.scenarios.support.vault_recovery import (
    query_rows,
    record_activity,
    submit_duplicate_rollbacks,
)

pytestmark = pytest.mark.scenario


async def test_child_edit_overlapping_directory_move_cannot_write_stale_path(
    app: Application,
) -> None:
    vault = app.vault("VaultMoveContentionVault")
    app.write(vault, "source/child.md", "before\n")
    await app.start()
    from core.vault_state.file_mutations import VaultMutationRejected

    checkpoint = app.events.checkpoint()
    move, edit = await move_with_competing_edit(vault)
    assert (move.source_path, move.destination_path, move.descendant_file_count) == (
        "source",
        "destination",
        1,
    )
    assert isinstance(edit, VaultMutationRejected | FileNotFoundError)
    if isinstance(edit, VaultMutationRejected):
        assert edit.code == "file_not_found"
    assert not (vault / "source").exists()
    assert (vault / "destination/child.md").read_text(encoding="utf-8") == "before\n"
    app.events.require(
        after=checkpoint,
        name="vault_directory_move_completed",
        fields={
            "vault_name": vault.name,
            "source_path": "source",
            "destination_path": "destination",
        },
    )
    rows = query_rows(
        app,
        "SELECT activity_id, path, related_path, operation, target_kind, status FROM vault_mutations WHERE activity_id IN (?, ?) ORDER BY id",
        ("move-contention", "edit-contention"),
    )
    assert rows == [
        {
            "activity_id": "move-contention",
            "path": "source",
            "related_path": "destination",
            "operation": "move",
            "target_kind": "directory",
            "status": "completed",
        }
    ]


async def test_concurrent_rollback_produces_one_durable_recovery_and_audit(
    app: Application,
) -> None:
    vault = app.vault("VaultConcurrentRollbackAuditVault")
    app.write(vault, "notes/existing.md", "before\n")
    await app.start()
    from core.vault_state.activity_rollback import (
        ActivityRollbackResult,
        ActivityRollbackUnavailable,
    )

    source = "concurrent-rollback-source"
    record_activity(
        app,
        vault,
        activity_id=source,
        kind="explorer",
        scope=None,
        label="Update file",
        mutations=[("update_vault_file", "notes/existing.md", "after\n")],
    )
    results = await submit_duplicate_rollbacks(app, vault, source)
    successes = [
        result for result in results if isinstance(result, ActivityRollbackResult)
    ]
    failures = [
        result for result in results if isinstance(result, ActivityRollbackUnavailable)
    ]
    assert len(successes) == 1 and len(failures) == 1
    result = successes[0]
    assert (result.source_activity_id, result.restored_count, result.deleted_count) == (
        source,
        1,
        0,
    )
    assert (vault / "notes/existing.md").read_text(encoding="utf-8") == "before\n"
    activities = query_rows(
        app,
        "SELECT activity_id, status, rollback_status, metadata_json FROM vault_activities WHERE activity_id IN (?, ?)",
        (source, result.rollback_activity_id),
    )
    by_id = {row["activity_id"]: row for row in activities}
    assert (
        by_id[source]["status"] == "completed"
        and by_id[source]["rollback_status"] == "completed"
    )
    assert by_id[result.rollback_activity_id]["status"] == "completed"
    assert (
        json.loads(by_id[result.rollback_activity_id]["metadata_json"])[
            "source_activity_id"
        ]
        == source
    )
    rollback_rows = query_rows(
        app,
        "SELECT activity_id, path, operation, before_snapshot_id, snapshot_ref FROM vault_mutations WHERE activity_id = ?",
        (result.rollback_activity_id,),
    )
    assert len(rollback_rows) == 1 and rollback_rows[0]["path"] == "notes/existing.md"
    assert rollback_rows[0]["before_snapshot_id"] and rollback_rows[0]["snapshot_ref"]
    snapshots = query_rows(
        app,
        "SELECT id, activity_id, path, snapshot_ref FROM file_snapshots WHERE activity_id = ?",
        (result.rollback_activity_id,),
    )
    assert any(
        row["id"] == rollback_rows[0]["before_snapshot_id"]
        and row["path"] == "notes/existing.md"
        and row["snapshot_ref"]
        for row in snapshots
    )
    source_preview = app.request(f"/api/vaults/{vault.name}/activity/{source}/rollback")
    assert (
        source_preview.status_code == 200
        and source_preview.json()["can_rollback"] is False
    )
    assert any(
        issue["code"] == "already_rolled_back"
        for issue in source_preview.json()["issues"]
    )
    all_rollbacks = query_rows(
        app,
        "SELECT activity_id FROM vault_activities WHERE json_extract(metadata_json, '$.source_activity_id') = ? AND status = 'completed'",
        (source,),
    )
    assert all_rollbacks == [{"activity_id": result.rollback_activity_id}]
