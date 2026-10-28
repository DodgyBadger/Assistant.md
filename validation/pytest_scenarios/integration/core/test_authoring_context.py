"""Real authoring boundaries with controlled history and configured test-model inputs."""

from __future__ import annotations

from datetime import datetime

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.authoring import assembly_history, run_assembly

pytestmark = pytest.mark.scenario


async def test_history_assembly_groups_tool_exchange(app: Application) -> None:
    vault = app.vault("AuthoringContextAssemblyVault")
    await app.start()
    from core.authoring.runtime import WorkflowAuthoringHost

    session_id = "context_assembly_session"
    workflow_id = f"{vault.name}/chat/{session_id}"
    now = datetime(2026, 4, 7, 12, 0, 0)
    history = assembly_history()
    host = WorkflowAuthoringHost(
        workflow_id=workflow_id,
        vault_path=str(vault),
        reference_date=now,
        session_key=session_id,
        chat_session_id=session_id,
        message_history=history,
    )
    checkpoint = app.events.checkpoint()
    output = await run_assembly(
        app, workflow_id=workflow_id, code=AUTHORING_CONTEXT_ASSEMBLY_CODE, host=host
    )
    app.events.require(
        "authoring_retrieve_history_completed",
        after=checkpoint,
        workflow_id=workflow_id,
        item_count=5,
    )
    app.events.require(
        "authoring_assemble_context_completed",
        after=checkpoint,
        workflow_id=workflow_id,
        message_count=7,
        instruction_count=1,
    )
    assert (
        output["history_count"] == 5
    ), "Expected retrieved history to group tool exchange atomically"
    assert output["roles"] == [
        "system",
        "user",
        "assistant",
        "tool_exchange",
        "user",
        "assistant",
        "user",
    ], "Expected assembled context ordering to preserve instructions and history"
    assert (
        output["last_message"] == "What should happen next?"
    ), "Expected explicit context message appended last"
    assert output["instruction_seen"], "Expected explicit instruction to be preserved"
    assert output[
        "clean_history_text"
    ], "Expected retrieved history string rendering to omit raw provider payload fields"
    assert output[
        "tool_exchange_rendered"
    ], "Expected retrieved tool exchange string rendering to include useful prompt text"


AUTHORING_CONTEXT_ASSEMBLY_CODE = """
history_payload = await retrieve_history(scope="session", limit="all")
assembled = await assemble_context(
    history=[
        *history_payload.items,
        {"role": "user", "content": "What should happen next?"},
    ],
    instructions="Use exact text.",
)

roles = []
instruction_seen = False
for message in assembled.messages:
    try:
        roles.append(message.role)
        if "Use exact text." in message.content:
            instruction_seen = True
    except AttributeError:
        roles.append("tool_exchange")

{
    "history_count": history_payload.item_count,
    "roles": roles,
    "last_message": assembled.messages[-1].content,
    "instruction_seen": instruction_seen,
    "clean_history_text": all(
        "message={" not in item.text and "provider_response_id" not in item.text
        for item in history_payload.items
    ),
    "tool_exchange_rendered": any(
        "tool_exchange: file_read" in item.text
        and "Trig notes file contents" in item.text
        for item in history_payload.items
    ),
}
"""
