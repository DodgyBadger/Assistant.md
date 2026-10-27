"""
Integration scenario for current Monty helper contract coverage.

Validates the current tool-first authoring surface using deterministic model
execution and stable validation events.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from validation.support.base_scenario import BaseScenario


class AuthoringContractScenario(BaseScenario):
    """Validate the implemented Monty host contract end to end."""

    async def test_scenario(self):
        vault = self.create_vault("AuthoringContractVault")

        self.create_file(vault, "notes/seed.md", "SEED_CONTENT")
        self.copy_files("validation/fixtures/files/test_image.jpg", vault, "images")
        self.create_file(vault, "notes/structured.md", STRUCTURED_NOTE)
        self.create_file(vault, "tasks/pending-one.md", "PENDING_ONE")

        self.create_file(
            vault,
            "AssistantMD/Authoring/authoring_contract_success.md",
            AUTHORING_CONTRACT_SUCCESS_WORKFLOW,
        )
        checkpoint = self.event_checkpoint()
        await self.start_system()
        startup_events = self.events_since(checkpoint)
        self.assert_event_contains(
            startup_events,
            name="workflow_loaded",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success"
            },
        )

        self.set_date("2026-04-06")

        checkpoint = self.event_checkpoint()
        result = await self.run_workflow(vault, "authoring_contract_success")
        self.soft_assert_equal(
            result.status, "completed", "Contract success workflow should complete"
        )
        events = self.events_since(checkpoint)

        self.assert_event_contains(
            events,
            name="authoring_monty_execution_started",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success"
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_direct_tool_started",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "tool": "file_read",
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_direct_tool_completed",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "tool": "file_read",
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_direct_tool_started",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "tool": "delegate",
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_direct_tool_completed",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "tool": "delegate",
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_retrieve_history_completed",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "item_count": 0,
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_assemble_context_completed",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "message_count": 3,
                "instruction_count": 1,
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_parse_markdown_started",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success"
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_parse_markdown_completed",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "heading_count": 2,
                "section_count": 2,
                "code_block_count": 1,
                "image_count": 1,
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_pending_files_filtered",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "pending_count": 1,
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_pending_files_completed",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "completed_count": 1,
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_finish_requested",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "status": "completed",
                "reason": "all-helpers-exercised",
            },
        )
        self.assert_event_contains(
            events,
            name="authoring_monty_execution_completed",
            expected={
                "workflow_id": "AuthoringContractVault/authoring_contract_success",
                "status": "completed",
                "reason": "all-helpers-exercised",
            },
        )

        contract_output = vault / "outputs" / "contract-success.md"
        self.soft_assert(
            contract_output.exists(), "Expected contract success output file"
        )
        if contract_output.exists():
            content = contract_output.read_text(encoding="utf-8")
            self.soft_assert(
                "SEED_CONTENT" in content, "Expected retrieved file content in output"
            )
            self.soft_assert(
                "RETURN_VALUE_CLEAN=True" in content,
                "Expected clean file return_value contract",
            )
            self.soft_assert(
                "CONTENT_NONE=True" in content,
                "Expected file read content side-channel to be empty",
            )
            self.soft_assert("ITEMS=1" in content, "Expected file read item projection")
            self.soft_assert(
                "IMAGE_RETURN_LIST=True" in content,
                "Expected image read multimodal return_value",
            )
            self.soft_assert(
                "IMAGE_CONTENT_NONE=True" in content,
                "Expected image read content side-channel to be empty",
            )
            self.soft_assert(
                "MARKDOWN_IMAGE_RETURN_LIST=True" in content,
                "Expected markdown image interleaved return_value",
            )
            self.soft_assert(
                "MARKDOWN_IMAGE_CONTENT_NONE=True" in content,
                "Expected markdown image content side-channel to be empty",
            )
            self.soft_assert(
                "ASSEMBLED=3" in content, "Expected assembled context count in output"
            )
            self.soft_assert(
                "HISTORY_ITEMS=0" in content,
                "Expected retrieve_history output in contract file",
            )

        delegate_output = vault / "outputs" / "delegate-success.md"
        self.soft_assert(delegate_output.exists(), "Expected delegate output file")
        if delegate_output.exists():
            self.soft_assert(
                delegate_output.stat().st_size > 0,
                "Expected delegate output file to be non-empty",
            )
        parsed_output = vault / "outputs" / "parse-markdown.md"
        self.soft_assert(parsed_output.exists(), "Expected markdown parse output file")
        if parsed_output.exists():
            parsed_content = parsed_output.read_text(encoding="utf-8")
            self.soft_assert(
                "Skill Example" in parsed_content,
                "Expected parsed frontmatter name in output",
            )
            self.soft_assert(
                "AI In Fiction" in parsed_content,
                "Expected parsed section heading in output",
            )
            self.soft_assert(
                "python" in parsed_content,
                "Expected parsed code block language in output",
            )
            self.soft_assert(
                "../images/test_image.jpg" in parsed_content,
                "Expected parsed image ref in output",
            )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()


AUTHORING_CONTRACT_SUCCESS_WORKFLOW_TEMPLATE = """---
run_type: workflow
enabled: false
description: Deterministic authoring contract success workflow
---

## Run

```python
async def _write_replace(path, content):
    existing = await file_read(operation="read", path=path)
    if existing.metadata.get("status") == "completed":
        await file_write(operation="write", path=path, content=content, overwrite=True)
    else:
        await file_write(operation="write", path=path, content=content)


source = await file_read(operation="read", path="notes/seed.md")
source_text = source.return_value
image_read = await file_read(operation="read", path="images/test_image.jpg")
markdown_image_read = await file_read(operation="read", path="notes/structured.md")
listing = await file_read(operation="list", path="notes")
history = await retrieve_history(scope="session", limit="all")
assembled = await assemble_context(
    instructions="Keep the response concise.",
    history=[
        *history.items,
        {"role": "user", "content": "Summarize the retrieved material."},
    ],
    context_messages=[{"role": "system", "content": "Validation context"}],
)
structured = await file_read(operation="read", path="notes/structured.md")
parsed = await parse_markdown(value=STRUCTURED_NOTE_PLACEHOLDER)
task_listing = await file_read(operation="list", path="tasks")
pending = await pending_files(operation="get", items=task_listing)
await pending_files(operation="complete", items=(pending.items[0],))

draft = await delegate(
    prompt=(
        "Seed:\\n"
        + source_text
        + "\\n\\nListing:\\n"
        + listing.return_value
        + "\\n\\nAssembled messages:\\n"
        + str(len(assembled.messages))
    ),
    instructions="Return one short deterministic line.",
    model="test",
    options={"thinking": False},
)

delegated = await delegate(
    prompt="Reply with a single word.",
    model="test",
)

await _write_replace(
    "outputs/contract-success.md",
    (
        source_text
        + f"\\nRETURN_VALUE_CLEAN={source_text == 'SEED_CONTENT'}"
        + f"\\nCONTENT_NONE={source.content is None}"
        + f"\\nITEMS={len(source.items)}"
        + f"\\nIMAGE_RETURN_LIST={isinstance(image_read.return_value, list)}"
        + f"\\nIMAGE_CONTENT_NONE={image_read.content is None}"
        + f"\\nMARKDOWN_IMAGE_RETURN_LIST={isinstance(markdown_image_read.return_value, list)}"
        + f"\\nMARKDOWN_IMAGE_CONTENT_NONE={markdown_image_read.content is None}"
        + f"\\nASSEMBLED={len(assembled.messages)}"
        + f"\\nHISTORY_ITEMS={history.item_count}"
    ),
)
await _write_replace(
    "outputs/delegate-draft-success.md",
    draft.return_value,
)
await _write_replace(
    "outputs/delegate-success.md",
    delegated.return_value,
)
await _write_replace(
    "outputs/parse-markdown.md",
    (
        f"name={parsed.frontmatter.get('name')}\\n"
        f"heading={parsed.sections[1].heading}\\n"
        f"code={parsed.code_blocks[0].language}\\n"
        f"image={parsed.images[0].src}"
    ),
)

await finish(status="completed", reason="all-helpers-exercised")
```
"""


STRUCTURED_NOTE = """---
name: Skill Example
description: Structured markdown sample
---

# Overview

General context.

## AI In Fiction

```python
print("hello")
```

![Example image](../images/test_image.jpg)
"""


AUTHORING_CONTRACT_SUCCESS_WORKFLOW = (
    AUTHORING_CONTRACT_SUCCESS_WORKFLOW_TEMPLATE.replace(
        "STRUCTURED_NOTE_PLACEHOLDER",
        repr(STRUCTURED_NOTE),
    )
)
