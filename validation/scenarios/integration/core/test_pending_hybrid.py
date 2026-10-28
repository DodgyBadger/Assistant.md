"""Real pending workflows distinguish controlled pre-completion and later mtimes."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.pending_inputs import (
    PENDING_HYBRID_WORKFLOW,
    append_with_mtime,
)
from validation.scenarios.support.vault_recovery import run_workflow

pytestmark = pytest.mark.scenario


async def test_pending_hybrid_ignores_old_mtime_edits_and_requeues_later_changes(
    app: Application,
) -> None:
    vault = app.vault("PendingHybridVault")
    app.write(vault, "tasks/task1.md", "Task 1\n")
    app.write(vault, "tasks/task2.md", "Task 2\n")
    app.write(vault, "AssistantMD/Authoring/pending_hybrid.md", PENDING_HYBRID_WORKFLOW)
    task1 = vault / "tasks/task1.md"
    original_mtime = task1.stat().st_mtime
    checkpoint = app.events.checkpoint()
    await app.start()
    workflow_id = f"{vault.name}/pending_hybrid"
    app.events.require("workflow_loaded", after=checkpoint, workflow_id=workflow_id)
    checkpoint = app.events.checkpoint()
    first = await run_workflow(app, vault, "pending_hybrid", expect_failure=False)
    assert first.status == "completed"
    app.events.require(
        "pending_files_resolved",
        after=checkpoint,
        workflow_id=workflow_id,
        pending_count=2,
    )

    # This is a controlled timestamp input. It tests pending classification,
    # without claiming that this external fixture edit was made by the workflow.
    append_with_mtime(task1, "self-edit\n", mtime=original_mtime)
    checkpoint = app.events.checkpoint()
    old_mtime_edit = await run_workflow(
        app, vault, "pending_hybrid", expect_failure=False
    )
    assert old_mtime_edit.status == "completed"
    app.events.require(
        "pending_files_resolved",
        after=checkpoint,
        workflow_id=workflow_id,
        pending_count=0,
    )

    append_with_mtime(task1, "user-edit\n", mtime=None)
    checkpoint = app.events.checkpoint()
    later_edit = await run_workflow(app, vault, "pending_hybrid", expect_failure=False)
    assert later_edit.status == "completed"
    app.events.require(
        "pending_files_resolved",
        after=checkpoint,
        workflow_id=workflow_id,
        pending_count=1,
        pending_paths=["tasks/task1"],
    )
    checkpoint = app.events.checkpoint()
    fourth = await run_workflow(app, vault, "pending_hybrid", expect_failure=False)
    assert fourth.status == "completed"
    app.events.require(
        "pending_files_resolved",
        after=checkpoint,
        workflow_id=workflow_id,
        pending_count=0,
    )
