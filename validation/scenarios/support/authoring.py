"""Controlled authoring inputs and real runtime invocation/evidence plumbing."""

from __future__ import annotations

import base64
import os
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.usage import RunUsage

if TYPE_CHECKING:
    from core.authoring.runtime import WorkflowAuthoringHost
    from core.memory.session_map.models import SessionMapDraft
    from core.vault_state.file_operations import VaultFileOperationResult

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from validation.scenarios.application import Application


def assembly_history() -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart(content="First question")], run_id="run-1"),
        ModelResponse(parts=[TextPart(content="First answer")], run_id="run-1"),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="file_read",
                    args={"path": "notes/trig.md"},
                    tool_call_id="call-read-trig",
                )
            ],
            run_id="run-1",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_read",
                    content="Trig notes file contents",
                    tool_call_id="call-read-trig",
                )
            ],
            run_id="run-1",
        ),
        ModelRequest(parts=[UserPromptPart(content="Second question")], run_id="run-2"),
        ModelResponse(parts=[TextPart(content="Second answer")], run_id="run-2"),
    ]


def contract_vault(app: Application) -> Path:
    vault = app.vault("AuthoringContractVault")
    app.write(vault, "notes/seed.md", "SEED_CONTENT")
    copy_contract_image(vault)
    app.write(vault, "notes/structured.md", STRUCTURED_NOTE)
    app.write(vault, "tasks/pending-one.md", "PENDING_ONE")
    app.write(
        vault,
        "AssistantMD/Authoring/authoring_contract_success.md",
        AUTHORING_CONTRACT_SUCCESS_WORKFLOW,
    )
    return vault


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


def active_tool_history() -> list[ModelMessage]:
    return [
        ModelRequest(
            parts=[UserPromptPart(content="Review these images.")], run_id="run-1"
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="file_read",
                    args={"operation": "read", "path": "Math/page_images/a.png"},
                    tool_call_id="call-image-1",
                )
            ],
            run_id="run-1",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_read",
                    content="Attached image 'Math/page_images/a.png'.",
                    tool_call_id="call-image-1",
                )
            ],
            run_id="run-1",
        ),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="file_read",
                    args={"operation": "read", "path": "Math/page_images/b.png"},
                    tool_call_id="call-image-2",
                )
            ],
            run_id="run-1",
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="file_read",
                    content="Attached image 'Math/page_images/b.png'.",
                    tool_call_id="call-image-2",
                )
            ],
            run_id="run-1",
        ),
    ]


def default_context_vaults(app: Application) -> tuple[Path, Path]:
    vault = app.vault("DefaultContextVault")
    app.write(
        vault,
        "AssistantMD/soul.md",
        "# Soul\n\nUse the validation soul instruction.\n"
        + "Soul filler that should eventually be truncated.\n" * 200,
    )
    app.write(
        vault,
        "AssistantMD/PlayBook.md",
        "# Vault Playbook\n\nUse the validation vault playbook.\n"
        + "Vault playbook filler that should eventually be truncated.\n" * 200,
    )
    app.write(
        vault,
        "AssistantMD/user.md",
        "# User Notes\n\n## User\n\n- The user works on AssistantMD.\n\n## Chat And Work Preferences\n\n- Prefer concise engineering answers.\n\n## Custom Section\n\n- Custom context note sections are owned by the skill, not the context script.\n\n## Large Section\n\n"
        + "- Filler context note line that should eventually be truncated.\n" * 500
        + "\n            ",
    )
    vault_without_instructions = app.vault("DefaultContextEmptyVault")
    app.write(
        vault,
        "Projects/WorkspaceA/readme.md",
        "# Workspace A\n\nThis workspace is for validating workspace README loading.\n",
    )
    app.write(
        vault,
        "Projects/WorkspaceA/PLAYBOOK.md",
        "# Workspace Playbook\n\nUse the workspace-specific validation playbook.\n",
    )
    app.write(
        vault,
        "Projects/WorkspaceB/notes.md",
        "# Workspace B Notes\n\nThis workspace intentionally has no README.\n",
    )
    icon_path = vault / "assets" / "file-icon.png"
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
        )
    )
    for source in (
        "AssistantMD/soul.md",
        "AssistantMD/PlayBook.md",
        "AssistantMD/user.md",
        "Projects/WorkspaceA/readme.md",
        "Projects/WorkspaceA/PLAYBOOK.md",
    ):
        path = vault / source
        path.write_text(
            f"![File icon]({os.path.relpath(icon_path, path.parent)})\n\n"
            + path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    for marker_relative_path, marker in (
        ("AssistantMD/soul.md", "SOUL_TAIL_SENTINEL"),
        ("AssistantMD/PlayBook.md", "VAULT_PLAYBOOK_TAIL_SENTINEL"),
        ("AssistantMD/user.md", "USER_NOTES_TAIL_SENTINEL"),
    ):
        marker_source = vault / marker_relative_path
        marker_source.write_text(
            marker_source.read_text(encoding="utf-8") + "\n" + marker, encoding="utf-8"
        )
    return (vault, vault_without_instructions)


def commit_compacted_history(
    vault: Path,
) -> tuple[str, list[ModelMessage], list[ModelMessage], SessionMapDraft]:
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.memory.session_map.checkpoints import (
            commit_session_map_context_checkpoint,
        )
        from core.memory.session_map.evidence import SessionMapEvidence
        from core.memory.session_map.models import (
            SessionMapDraft,
            SessionMapEntry,
            SessionMapEntryBasis,
            SessionMapEntryKind,
            SessionMapEntryState,
            SessionMapTrajectory,
            SourceRange,
        )
        from core.runtime.state import get_runtime_context

        compacted_session_id = "default_context_compacted_session"
        store = get_runtime_context().chat_store
        store.ensure_session(
            compacted_session_id, vault.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        compacted_raw_messages: list[ModelMessage] = [
            ModelRequest(
                parts=[UserPromptPart(content="CANONICAL_ONLY_EVICTED_SENTINEL")],
                run_id="run-compacted",
            ),
            ModelResponse(
                parts=[TextPart(content="I will retain the active objective.")],
                run_id="run-compacted",
            ),
            ModelRequest(
                parts=[UserPromptPart(content="Inspect the current artifact.")],
                run_id="run-tail",
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="file_read",
                        args={"operation": "read", "path": "artifact.md"},
                        tool_call_id="call-compacted-tail",
                    )
                ],
                run_id="run-tail",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="file_read",
                        content="Current artifact evidence.",
                        tool_call_id="call-compacted-tail",
                    )
                ],
                run_id="run-tail",
            ),
            ModelResponse(
                parts=[TextPart(content="The artifact is current.")], run_id="run-tail"
            ),
        ]
        store.add_messages(compacted_session_id, vault.name, compacted_raw_messages)
        compacted_revision = store.get_session_history_revision(
            compacted_session_id, vault.name
        )
        session_map = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The session established an artifact-review objective.",
                sources=(SourceRange(start=0, end=1),),
            ),
            entries=(
                SessionMapEntry(
                    id="artifact_review",
                    kind=SessionMapEntryKind.GOAL,
                    state=SessionMapEntryState.ACTIVE,
                    basis=SessionMapEntryBasis.USER_ESTABLISHED,
                    text="Review the current artifact.",
                    sources=(SourceRange(start=0, end=1),),
                ),
            ),
        )
        commit_session_map_context_checkpoint(
            store=store,
            session_id=compacted_session_id,
            vault_name=vault.name,
            draft=session_map,
            previous_map=SessionMapDraft(),
            new_evidence=(
                SessionMapEvidence(
                    evidence_id="default-context-compacted-envelope",
                    session_id=compacted_session_id,
                    vault_name=vault.name,
                    history_revision=compacted_revision,
                    source_start_sequence_index=0,
                    source_end_sequence_index=1,
                    message_count=2,
                    estimated_tokens=20,
                    projected_text="Compacted objective evidence.",
                    source_digest="a" * 64,
                    citable_source_ranges=(SourceRange(start=0, end=1),),
                ),
            ),
            expected_history_revision=compacted_revision,
            message_count_before=len(compacted_raw_messages),
            source="validation",
            checkpoint_id="default-context-compacted-checkpoint",
        )
        effective_history = store.get_history(compacted_session_id, vault.name) or []
        return (
            compacted_session_id,
            compacted_raw_messages,
            effective_history,
            session_map,
        )


def copy_contract_image(vault: Path) -> None:
    destination = vault / "images"
    destination.mkdir(parents=True, exist_ok=True)
    source = (
        Path(__file__).resolve().parents[3] / "validation/fixtures/files/test_image.jpg"
    )
    shutil.copy2(source, destination / source.name)


async def run_contract_workflow(app: Application, vault: Path) -> dict[str, Any]:
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskSource
    from core.runtime.state import get_runtime_context

    result = await get_runtime_context().workflow_governor.execute_workflow(
        global_id=f"{vault.name}/authoring_contract_success",
        source=ExecutionTaskSource.API,
        authority=LOCAL_USER_AUTHORITY,
    )
    payload = result.to_dict()
    app.evidence.append("authoring-workflow-results.jsonl", payload)
    return payload


async def run_assembly(
    app: Application, *, workflow_id: str, code: str, host: WorkflowAuthoringHost
) -> dict[str, Any]:
    from core.authoring.runtime import run_authoring_monty
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        result = await run_authoring_monty(
            workflow_id=workflow_id,
            code=code,
            host=host,
            script_name="context_assembly.py",
        )
    assert isinstance(result.value, dict)
    app.evidence.save("assembled-context.json", result.value)
    return result.value


async def process_context(
    app: Application,
    processor: Callable[
        [RunContext[Any], list[ModelMessage]], Awaitable[list[ModelMessage]]
    ],
    prompt: str,
    messages: list[ModelMessage],
) -> list[ModelMessage]:
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    from pydantic_ai.models import Model

    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.llm.model_factory import build_model_instance

    model = build_model_instance("test")
    assert isinstance(model, Model)
    context = RunContext(deps=None, model=model, usage=RunUsage(), prompt=prompt)
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        processed = await processor(context, messages)
    app.evidence.append(
        "processed-context.jsonl",
        {
            "prompt": prompt,
            "messages": ModelMessagesTypeAdapter.dump_python(processed, mode="json"),
        },
    )
    return processed


def system_prompt_text(messages: list[ModelMessage]) -> str:
    from pydantic_ai.messages import SystemPromptPart

    return "\n\n".join(
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, SystemPromptPart)
    )


def read_markdown_with_media(vault: Path, path: str) -> VaultFileOperationResult:
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.vault_state.file_operations import read_vault_file_operation

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        return read_vault_file_operation(vault_path=str(vault), path=path)
