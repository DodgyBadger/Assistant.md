"""Integration scenario for pending_files diff metadata."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.support.base_scenario import BaseScenario


class PendingDiffMetadataScenario(BaseScenario):
    """Validate pending_files get returns diffs from last completed baseline."""

    async def test_scenario(self):
        vault = self.create_vault("PendingDiffVault")
        self.create_file(vault, "meetings/log.md", "# Meeting Log\n\n- Original note\n")
        self.create_file(
            vault,
            "AssistantMD/Authoring/pending_diff.md",
            PENDING_DIFF_WORKFLOW,
        )

        await self.start_system()

        checkpoint = self.event_checkpoint()
        result = await self.run_workflow(vault, "pending_diff")
        events = self.events_since(checkpoint)
        self.soft_assert_equal(
            result.status, "completed", "Initial run should complete"
        )
        self.assert_event_contains(
            events,
            name="pending_files_snapshots_recorded",
            expected={
                "workflow_id": "PendingDiffVault/pending_diff",
                "snapshot_count": 1,
            },
        )
        self.soft_assert_equal(
            (vault / "outputs" / "pending-diff.md").exists(),
            False,
            "Initial run should not write a diff because no baseline existed at get time",
        )

        meeting_log = vault / "meetings" / "log.md"
        meeting_log.write_text(
            meeting_log.read_text(encoding="utf-8") + "- New action item\n",
            encoding="utf-8",
        )

        checkpoint = self.event_checkpoint()
        result = await self.run_workflow(vault, "pending_diff")
        events = self.events_since(checkpoint)
        self.soft_assert_equal(result.status, "completed", "Second run should complete")
        self.assert_event_contains(
            events,
            name="pending_files_resolved",
            expected={
                "workflow_id": "PendingDiffVault/pending_diff",
                "pending_count": 1,
                "pending_paths": ["meetings/log"],
            },
        )
        diff_output = vault / "outputs" / "pending-diff.md"
        self.soft_assert(
            diff_output.exists(), "Second run should write pending diff output"
        )
        if diff_output.exists():
            diff_text = diff_output.read_text(encoding="utf-8")
            self.soft_assert(
                "available=True" in diff_text, "Diff metadata should be available"
            )
            self.soft_assert(
                "+- New action item" in diff_text, "Diff should include added note"
            )
            self.soft_assert(
                "snapshot_set_id=" in diff_text, "Diff should identify snapshot set"
            )
            self.soft_assert(
                "file_snapshot_id=" in diff_text, "Diff should identify file snapshot"
            )

        checkpoint = self.event_checkpoint()
        result = await self.run_workflow(vault, "pending_diff")
        events = self.events_since(checkpoint)
        self.soft_assert_equal(result.status, "completed", "Third run should complete")
        self.assert_event_contains(
            events,
            name="pending_files_resolved",
            expected={
                "workflow_id": "PendingDiffVault/pending_diff",
                "pending_count": 0,
            },
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()


PENDING_DIFF_WORKFLOW = """---
run_type: workflow
enabled: false
description: Pending diff metadata validation
---

## Run

```python
listed = await file_read(operation="list", path="meetings")
pending = await pending_files(operation="get", items=listed)
if pending.items:
    first = pending.items[0]
    diff = first.metadata.get("pending_diff", {})
    if diff.get("available"):
        await file_write(
            operation="write",
            path="outputs/pending-diff.md",
            content=(
                f"available={diff.get('available')}\\n"
                f"snapshot_set_id={diff.get('snapshot_set_id')}\\n"
                f"file_snapshot_id={diff.get('file_snapshot_id')}\\n"
                + diff.get("text", "")
            ),
        )
    await pending_files(operation="complete", items=pending.items)
await finish(status="completed", reason="pending-diff-done")
```
"""
