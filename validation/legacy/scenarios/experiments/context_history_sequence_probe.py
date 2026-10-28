"""
Diagnostic scenario for inspecting context-manager message history sequences.

This intentionally writes a readable artifact instead of asserting final policy.
Use it when investigating duplicated turns around context assembly and latest
message handling.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from core.chat.chat_store import ChatStore
from core.chat.history_service import ChatHistoryContext, ChatHistoryService
from core.utils.messages import extract_role_and_text
from validation.support.base_scenario import BaseScenario


class ContextHistorySequenceProbeScenario(BaseScenario):
    """Write message-sequence artifacts for context-manager history debugging."""

    async def test_scenario(self):
        vault = self.create_vault("ContextHistorySequenceProbeVault")
        self.create_file(
            vault,
            "AssistantMD/Authoring/history_probe.md",
            HISTORY_PROBE_TEMPLATE,
        )

        await self.start_system()

        from core.authoring.context_manager import (
            build_context_manager_history_processor,
        )

        session_id = "context_history_sequence_probe_session"
        store = ChatStore()
        persisted_messages = _persisted_fixture_messages()
        store.add_messages(session_id, vault.name, persisted_messages)

        processor = build_context_manager_history_processor(
            session_id=session_id,
            vault_name=vault.name,
            vault_path=str(vault),
            model_alias="gpt",
            template_name="history_probe.md",
        )

        current_user_message = ModelRequest(
            parts=[UserPromptPart(content="Current user request")],
            run_id="run-current",
        )
        history_only_input = list(persisted_messages)
        history_plus_current_input = [*persisted_messages, current_user_message]

        history_only_output = await processor(
            SimpleNamespace(prompt="Current user request", deps=SimpleNamespace()),
            list(history_only_input),
        )
        history_plus_current_output = await processor(
            SimpleNamespace(prompt="Current user request", deps=SimpleNamespace()),
            list(history_plus_current_input),
        )

        retrieved = ChatHistoryService().get_conversation_history(
            context=ChatHistoryContext(
                message_history=tuple(history_only_input),
                session_id=session_id,
                vault_name=vault.name,
            ),
            scope="session",
            limit="all",
        )

        report = _render_report(
            session_id=session_id,
            persisted=persisted_messages,
            retrieved=retrieved.items,
            history_only_input=history_only_input,
            history_only_output=history_only_output,
            history_plus_current_input=history_plus_current_input,
            history_plus_current_output=history_plus_current_output,
        )
        artifact_path = self.artifacts_dir / "context_history_sequence_probe.md"
        artifact_path.write_text(report, encoding="utf-8")

        print(f"Context history sequence probe written to {artifact_path}")

        await self.stop_system()
        self.teardown_scenario()


def _persisted_fixture_messages() -> list:
    return [
        ModelRequest(
            parts=[UserPromptPart(content="First user message")], run_id="run-1"
        ),
        ModelResponse(
            parts=[TextPart(content="First assistant response")], run_id="run-1"
        ),
        ModelRequest(
            parts=[UserPromptPart(content="Second user message")], run_id="run-2"
        ),
        ModelResponse(
            parts=[TextPart(content="Second assistant response")], run_id="run-2"
        ),
    ]


def _render_report(
    *,
    session_id: str,
    persisted: list,
    retrieved: tuple,
    history_only_input: list,
    history_only_output: list,
    history_plus_current_input: list,
    history_plus_current_output: list,
) -> str:
    lines = [
        "# Context History Sequence Probe",
        "",
        f"- session_id: `{session_id}`",
        "",
        "## Persisted Store Fixture",
        _render_model_messages(persisted),
        "",
        "## retrieve_history Broker View",
        _render_retrieved_items(retrieved),
        "",
        "## Case A: Processor Input Is Persisted History Only",
        "",
        "### Input",
        _render_model_messages(history_only_input),
        "",
        "### Compiled Output",
        _render_model_messages(history_only_output),
        "",
        "### Duplicate Adjacent Texts",
        _render_duplicate_report(history_only_output),
        "",
        "## Case B: Processor Input Includes Current User Prompt",
        "",
        "### Input",
        _render_model_messages(history_plus_current_input),
        "",
        "### Compiled Output",
        _render_model_messages(history_plus_current_output),
        "",
        "### Duplicate Adjacent Texts",
        _render_duplicate_report(history_plus_current_output),
        "",
    ]
    return "\n".join(lines)


def _render_model_messages(messages: list) -> str:
    if not messages:
        return "_No messages._"
    lines = []
    for index, message in enumerate(messages):
        role, text = extract_role_and_text(message)
        parts = getattr(message, "parts", ()) or ()
        part_kinds = ", ".join(
            str(getattr(part, "part_kind", type(part).__name__)) for part in parts
        )
        run_id = getattr(message, "run_id", None)
        lines.append(
            f"{index}. `{type(message).__name__}` role=`{role}` run_id=`{run_id}` "
            f"parts=`{part_kinds}` text={text!r}"
        )
    return "\n".join(lines)


def _render_retrieved_items(items: tuple) -> str:
    if not items:
        return "_No retrieved items._"
    lines = []
    for index, item in enumerate(items):
        role = getattr(item, "role", "tool_exchange")
        message_type = getattr(item, "message_type", None)
        sequence_index = getattr(item, "sequence_index", None)
        text = getattr(item, "content", "") or getattr(item, "result_text", "") or ""
        lines.append(
            f"{index}. `{type(item).__name__}` role=`{role}` "
            f"message_type=`{message_type}` sequence_index=`{sequence_index}` text={text!r}"
        )
    return "\n".join(lines)


def _render_duplicate_report(messages: list) -> str:
    duplicates = []
    previous = None
    for index, message in enumerate(messages):
        role, text = extract_role_and_text(message)
        current = (role, text)
        if previous == current:
            duplicates.append(
                f"- adjacent duplicate ending at index {index}: role=`{role}` text={text!r}"
            )
        previous = current
    return "\n".join(duplicates) if duplicates else "_No adjacent duplicates detected._"


HISTORY_PROBE_TEMPLATE = """---
run_type: context
description: Diagnostic template that preserves retrieved history without extra context.
---
```python
history_result = await retrieve_history(scope="session", limit="all")
history = list(history_result.items)

await assemble_context(history=history)
```
"""
