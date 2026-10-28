"""Actual pending completion snapshots supply diff metadata after a later file edit."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.pending_inputs import PENDING_DIFF_WORKFLOW
from validation.pytest_scenarios.support.vault_recovery import run_workflow

pytestmark = pytest.mark.scenario


async def test_pending_diff_uses_last_completed_snapshot_and_advances_baseline(
    app: Application,
) -> None:
    vault = app.vault("PendingDiffVault")
    app.write(vault, "meetings/log.md", "# Meeting Log\n\n- Original note\n")
    app.write(vault, "AssistantMD/Authoring/pending_diff.md", PENDING_DIFF_WORKFLOW)
    await app.start()
    workflow_id = f"{vault.name}/pending_diff"
    diff_output = vault / "outputs/pending-diff.md"
    checkpoint = app.events.checkpoint()
    initial = await run_workflow(app, vault, "pending_diff", expect_failure=False)
    assert initial.status == "completed"
    app.events.require(
        "pending_files_snapshots_recorded",
        after=checkpoint,
        workflow_id=workflow_id,
        snapshot_count=1,
    )
    assert not diff_output.exists()

    meeting_log = vault / "meetings/log.md"
    meeting_log.write_text(
        meeting_log.read_text(encoding="utf-8") + "- New action item\n",
        encoding="utf-8",
    )
    checkpoint = app.events.checkpoint()
    changed = await run_workflow(app, vault, "pending_diff", expect_failure=False)
    assert changed.status == "completed"
    app.events.require(
        "pending_files_resolved",
        after=checkpoint,
        workflow_id=workflow_id,
        pending_count=1,
        pending_paths=["meetings/log"],
    )
    assert diff_output.exists()
    diff_text = diff_output.read_text(encoding="utf-8")
    # This fixture deliberately exports these machine-consumed metadata fields.
    assert "available=True" in diff_text
    assert "+- New action item" in diff_text
    assert "snapshot_set_id=" in diff_text
    assert "file_snapshot_id=" in diff_text

    checkpoint = app.events.checkpoint()
    unchanged = await run_workflow(app, vault, "pending_diff", expect_failure=False)
    assert unchanged.status == "completed"
    app.events.require(
        "pending_files_resolved",
        after=checkpoint,
        workflow_id=workflow_id,
        pending_count=0,
    )
