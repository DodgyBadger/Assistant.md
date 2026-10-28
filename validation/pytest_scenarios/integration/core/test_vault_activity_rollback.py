"""Real vault recovery behavior and durable outcomes."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_activity_rollback_conflicts_chaining_and_directory_limits(
    app: Application,
) -> None:
    import json

    from validation.pytest_scenarios.support.vault_recovery import (
        activity_rows,
        execute_rollback,
        preview_rollback,
        record_activity,
    )

    vault = app.vault("VaultActivityRollbackVault")
    app.write(vault, "notes/existing.md", "original\n")
    await app.start()
    source_id = "activity_rollback_source"
    record_activity(
        app,
        vault,
        activity_id=source_id,
        kind="chat",
        scope="chat_session:rollback-test",
        label="Update rollback fixtures",
        mutations=[
            ("replace_text", "notes/existing.md", "first update\n"),
            ("replace_text", "notes/existing.md", "final update\n"),
            ("write", "notes/created.md", "created\n"),
            ("replace_text", "notes/created.md", "created and edited\n"),
        ],
    )

    preview = preview_rollback(app, vault.name, source_id)
    assert preview["can_rollback"] is True
    assert preview["restore_count"] == 1
    assert preview["delete_count"] == 1
    assert {item["path"] for item in preview["paths"]} == {
        "notes/created.md",
        "notes/existing.md",
    }
    later_id = "activity_rollback_later_edit"
    record_activity(
        app,
        vault,
        activity_id=later_id,
        kind="explorer",
        scope=None,
        label="Edit existing.md",
        mutations=[("update_vault_file", "notes/existing.md", "later manual edit\n")],
    )

    blocked = preview_rollback(app, vault.name, source_id)
    assert blocked["can_rollback"] is False
    assert any(issue["code"] == "state_conflict" for issue in blocked["issues"])
    rejected = execute_rollback(app, vault.name, source_id, blocked)
    assert rejected.status_code == 409
    assert (vault / "notes/created.md").read_text(
        encoding="utf-8"
    ) == "created and edited\n", "A blocked rollback must not change any other path"

    later_preview = preview_rollback(app, vault.name, later_id)
    later_rollback = execute_rollback(app, vault.name, later_id, later_preview)
    assert later_rollback.status_code == 200
    assert (vault / "notes/existing.md").read_text(encoding="utf-8") == "final update\n"

    available_again = preview_rollback(app, vault.name, source_id)
    assert (
        available_again["can_rollback"] is True
    ), "Restoring the later edit should re-enable the earlier activity rollback"
    rollback_checkpoint = app.events.checkpoint()
    source_rollback = execute_rollback(app, vault.name, source_id, available_again)
    assert source_rollback.status_code == 200
    source_result = source_rollback.json()
    assert source_result["source_activity_id"] == source_id
    rollback_activity_id = source_result["rollback_activity_id"]
    assert (vault / "notes/existing.md").read_text(encoding="utf-8") == "original\n"
    assert not (vault / "notes/created.md").exists()
    app.events.require(
        after=rollback_checkpoint,
        name="vault_activity_rollback_completed",
        fields={
            "source_activity_id": source_id,
            "rollback_activity_id": rollback_activity_id,
            "vault_name": vault.name,
            "restored_count": 1,
            "deleted_count": 1,
        },
    )

    source_after = preview_rollback(app, vault.name, source_id)
    assert source_after["can_rollback"] is False
    assert any(
        issue["code"] == "already_rolled_back" for issue in source_after["issues"]
    )

    undo_preview = preview_rollback(app, vault.name, rollback_activity_id)
    assert undo_preview["can_rollback"] is True
    undo = execute_rollback(app, vault.name, rollback_activity_id, undo_preview)
    assert undo.status_code == 200
    assert (vault / "notes/existing.md").read_text(encoding="utf-8") == "final update\n"
    assert (vault / "notes/created.md").read_text(
        encoding="utf-8"
    ) == "created and edited\n"

    rows = activity_rows(app)
    source_row = next(row for row in rows if row["activity_id"] == source_id)
    rollback_row = next(
        row for row in rows if row["activity_id"] == rollback_activity_id
    )
    assert source_row["status"] == "completed"
    assert source_row["rollback_status"] == "completed"
    assert json.loads(rollback_row["metadata_json"])["source_activity_id"] == source_id

    directory = app.request(
        f"/api/vaults/{vault.name}/paths/mutate",
        method="POST",
        data={"operation": "create_directory", "path": "unsupported-directory"},
    )
    assert directory.status_code == 200
    activity = app.request(f"/api/vaults/{vault.name}/activity").json()
    directory_activity = next(
        group
        for group in activity["groups"]
        if any(
            mutation["target_kind"] == "directory"
            and mutation["path"] == "unsupported-directory"
            for mutation in group["mutations"]
        )
    )
    directory_preview = preview_rollback(
        app, vault.name, directory_activity["activity_id"]
    )
    assert directory_preview["can_rollback"] is False
    assert any(
        issue["code"] == "unsupported_directory_operation"
        for issue in directory_preview["issues"]
    )


async def test_duplicate_activity_rollback_submissions_reject_one(
    app: Application,
) -> None:
    from core.vault_state.activity_rollback import (
        ActivityRollbackResult,
        ActivityRollbackUnavailable,
    )
    from validation.pytest_scenarios.support.vault_recovery import (
        record_activity,
        submit_duplicate_rollbacks,
    )

    vault = app.vault("VaultActivityConcurrentRollbackVault")
    app.write(vault, "notes/concurrent-rollback.md", "before\n")
    await app.start()
    source_id = "activity_concurrent_rollback_source"
    record_activity(
        app,
        vault,
        activity_id=source_id,
        kind="explorer",
        scope=None,
        label="Concurrent rollback fixture",
        mutations=[("update_vault_file", "notes/concurrent-rollback.md", "after\n")],
    )

    results = await submit_duplicate_rollbacks(app, vault, source_id)
    assert sum(isinstance(result, ActivityRollbackResult) for result in results) == 1
    assert (
        sum(isinstance(result, ActivityRollbackUnavailable) for result in results) == 1
    ), "A duplicate rollback submission should be rejected"
    assert (vault / "notes/concurrent-rollback.md").read_text(
        encoding="utf-8"
    ) == "before\n"
    # This proves outcomes, not the original paused-restore waiting/call-count claim.
