"""
Basic haiku workflow happy-path scenario.

Runs a real workflow end-to-end with a live model to provide
human-reviewable artifacts.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from validation.core.base_scenario import BaseScenario


class BasicHaikuWorkflowScenario(BaseScenario):
    """Golden-path workflow execution with multimodal input and file outputs."""

    async def test_scenario(self):
        """Execute complete end-to-end workflow: system startup → workflow execution."""

        # === SETUP ===
        vault = self.create_vault("HaikuVault")

        self.create_file(
            vault, "AssistantMD/Authoring/haiku_writer.md", HAIKU_WRITER_WORKFLOW
        )
        self.copy_files("validation/templates/files/test_image.jpg", vault, "images")
        self.create_file(vault, "notes/haiku_seed.md", HAIKU_SEED_NOTE)

        # === SYSTEM STARTUP VALIDATION ===
        checkpoint = self.event_checkpoint()
        await self.start_system()

        events = self.events_since(checkpoint)
        self.assert_event_contains(
            events,
            name="workflow_loaded",
            expected={"workflow_id": "HaikuVault/haiku_writer"},
        )
        job_event = self.assert_event_contains(
            events,
            name="workflow_job_created",
            expected={"job_id": "HaikuVault__haiku_writer", "action": "created"},
        )
        trigger = str(job_event.get("data", {}).get("trigger", "")).lower()
        assert "cron" in trigger, "Expected cron trigger"

        # === WORKFLOW EXECUTION ===
        self.set_date("2025-01-15")  # Wednesday

        checkpoint = self.event_checkpoint()
        assert await self.trigger_job(
            vault,
            "haiku_writer",
            timeout_seconds=30.0,
        ), "Job should execute when triggered"

        events = self.events_since(checkpoint)
        self.assert_event_contains(
            events,
            name="job_executed",
            expected={"job_id": "HaikuVault__haiku_writer"},
        )
        self.assert_event_contains(
            events,
            name="authoring_direct_tool_completed",
            expected={"workflow_id": "HaikuVault/haiku_writer"},
        )

        output_path = vault / "haiku-2025-01-15.md"
        assert output_path.exists(), "Expected haiku-2025-01-15.md to be created"
        assert output_path.stat().st_size > 0, "Output file is empty"

        await self.stop_system()
        self.teardown_scenario()


# === WORKFLOW TEMPLATE ===

HAIKU_WRITER_WORKFLOW = """---
run_type: workflow
schedule: cron: 0 9 * * *
enabled: true
description: Haiku writing workflow
---
```python
\"\"\"Write one haiku from the seed note and save it to today's output file.\"\"\"

source = await file_read(operation="read", path="notes/haiku_seed.md")
note_content = source.return_value

draft = await delegate(
    prompt=(
        "Write a three-line haiku inspired by this note. Preserve the imagery.\\n\\n"
        + note_content
    ),
    instructions="Write only the haiku with proper line breaks.",
    model="gpt-mini",
)

await file_write(
    operation="write",
    path=f"haiku-{date.today()}.md",
    content=draft.return_value,
)
```
"""


HAIKU_SEED_NOTE = """Morning frost settles over the fence.
The garden is quiet except for a single bird call.
Sunlight is just starting to reach the grass.
"""
