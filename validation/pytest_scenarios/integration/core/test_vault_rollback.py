"""Real vault recovery behavior and durable outcomes."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_failed_workflow_rolls_back_files_and_reports_partial_directory_recovery(
    app: Application,
) -> None:
    from validation.pytest_scenarios.support.vault_recovery import (
        FAILING_PROBE_WORKFLOW,
        activity_status,
        mutation_rows,
        rollback_task,
        run_workflow,
        snapshot_status,
    )

    vault = app.vault("VaultStateRollbackVault")
    app.write(vault, "notes/preexisting-append.md", "Original append\n")
    app.write(vault, "notes/preexisting-delete.md", "Original delete\n")
    app.write(vault, "notes/move-source.md", "Original move source\n")
    app.write(vault, "AssistantMD/Authoring/failing_probe.md", FAILING_PROBE_WORKFLOW)
    await app.start()

    checkpoint = app.events.checkpoint()
    with pytest.raises(RuntimeError):
        await run_workflow(app, vault, "failing_probe", expect_failure=True)
    failed_event = app.events.require(
        after=checkpoint,
        name="workflow_task_failed",
        fields={
            "workflow_id": f"{vault.name}/failing_probe",
            "status": "failed",
            "failure_kind": "unknown",
            "retryable": False,
        },
    )
    task_id = failed_event["data"]["task_id"]
    task_detail = app.request(f"/api/tasks/{task_id}")
    assert (
        task_detail.status_code == 200
    ), "Failed workflow task detail should be available"
    assert (
        task_detail.json()["status"] == "failed"
    ), "Workflow failure should be reported"
    workflow_failure = task_detail.json().get("metadata", {}).get("workflow_failure")
    assert isinstance(
        workflow_failure, dict
    ), "Failed workflow task should expose structured recovery metadata"
    if isinstance(workflow_failure, dict):
        assert (
            workflow_failure.get("failure_kind") == "unknown"
        ), "Unexpected workflow exceptions should preserve fail-fast unknown classification"
        assert (
            workflow_failure.get("retryable") is False
        ), "Unexpected workflow exceptions should not be marked retryable"
        assert (
            workflow_failure.get("workflow_id") == f"{vault.name}/failing_probe"
        ), "Workflow failure metadata should identify the failed workflow"
        assert isinstance(
            workflow_failure.get("recovery_summary"), dict
        ), "Workflow failure metadata should include a compact recovery summary"
    app.events.require(
        after=checkpoint,
        name="task_rollback_started",
        fields={"task_id": task_id, "terminal_status": "failed"},
    )
    app.events.require(
        after=checkpoint,
        name="task_rollback_completed",
        fields={
            "task_id": task_id,
            "terminal_status": "failed",
            "rollback_status": "partial",
            "nonrollbackable_mutation_rows": 1,
        },
    )
    assert not (
        vault / "notes/created-before-failure.md"
    ).exists(), "Rollback should delete files created by the failed workflow"
    assert (vault / "notes/preexisting-append.md").read_text(
        encoding="utf-8"
    ) == "Original append\n", "Rollback should restore appended file content"
    assert (vault / "notes/preexisting-delete.md").read_text(
        encoding="utf-8"
    ) == "Original delete\n", "Rollback should restore deleted file content"
    assert (vault / "notes/move-source.md").read_text(
        encoding="utf-8"
    ) == "Original move source\n", "Rollback should restore moved source content"
    assert not (
        vault / "notes/move-destination.md"
    ).exists(), "Rollback should remove moved destination file"
    assert (
        vault / "notes/directory-before-failure"
    ).is_dir(), "Directory actions should remain when task rollback has no retained directory snapshot"

    retained_status = snapshot_status(app, task_id)
    assert (
        retained_status == "rolled_back"
    ), "Task snapshot should be marked rolled back"
    assert activity_status(app, task_id) == (
        "failed",
        "partial",
    ), "Task activity should expose a partial rollback when directory actions remain"

    retry_result = rollback_task(
        app, task_id=task_id, terminal_status="failed", reason="validation retry"
    )
    assert retry_result.skipped, "Second rollback should be skipped"
    assert (
        retry_result.reason == "already_rolled_back"
    ), "Second rollback should report already rolled back"
    assert retry_result.mutation_rows_seen == len(
        mutation_rows(app, task_id)
    ), "Second rollback should still report retained mutation rows"
    assert not (
        vault / "notes/created-before-failure.md"
    ).exists(), "Second rollback should leave created file deleted"
    assert (vault / "notes/preexisting-append.md").read_text(
        encoding="utf-8"
    ) == "Original append\n", (
        "Second rollback should not change restored append content"
    )


async def test_task_rollback_conflict_preserves_every_path(app: Application) -> None:
    from core.vault_state.file_mutations import VaultMutationRejected
    from validation.pytest_scenarios.support.vault_recovery import (
        conflicting_task,
        rollback_task,
    )

    vault = app.vault("VaultStateRollbackConflictVault")
    app.write(vault, "notes/preexisting-append.md", "Original append\n")
    await app.start()
    conflict_path = vault / "notes/preexisting-append.md"
    untouched_path = vault / "notes/created-before-failure.md"
    checkpoint = app.events.checkpoint()
    async with conflicting_task(app, vault) as task_id:
        conflict_path.write_text("Later external edit\n", encoding="utf-8")

    app.events.require(
        "task_rollback_failed",
        after=checkpoint,
        task_id=task_id,
        terminal_status="failed",
        error_type="VaultMutationRejected",
    )

    with pytest.raises(VaultMutationRejected) as rejected:
        rollback_task(
            app,
            task_id=task_id,
            terminal_status="failed",
            reason="validation conflict rollback",
        )
    assert (
        rejected.value.code == "file_conflict"
    ), "Task rollback should report a stale current state"
    assert (
        conflict_path.read_text(encoding="utf-8") == "Later external edit\n"
    ), "Rejected task rollback must preserve the later file edit"
    assert (
        untouched_path.read_text(encoding="utf-8") == "created then rolled back\n"
    ), "A conflict must prevent rollback of every other path in the vault"
