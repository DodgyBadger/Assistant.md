"""Real authoring boundaries with controlled history and configured test-model inputs."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.authoring import (
    active_tool_history,
    process_context,
)

pytestmark = pytest.mark.scenario


async def test_context_preserves_active_tool_turn(app: Application) -> None:
    vault = app.vault("ContextToolPassthroughVault")
    await app.start()
    from core.authoring.context_manager import build_context_manager_history_processor

    session_id = "context_tool_passthrough_session"
    processor = build_context_manager_history_processor(
        session_id=session_id,
        vault_name=vault.name,
        vault_path=str(vault),
        model_alias="gpt",
        template_name="default.md",
    )
    original_messages = active_tool_history()
    checkpoint = app.events.checkpoint()
    processed = await process_context(
        app, processor, "Review these images.", list(original_messages)
    )
    app.events.require(
        "context_history_passthrough",
        after=checkpoint,
        session_id=session_id,
        vault_name=vault.name,
        template_name="default.md",
        reason="latest_turn_contains_tool_parts",
    )
    assert len(processed) == len(
        original_messages
    ), "Expected passthrough to preserve message count for active tool turns"
    assert [type(message).__name__ for message in processed] == [
        type(message).__name__ for message in original_messages
    ], "Expected passthrough to preserve provider-native message types"
    assert (
        getattr(processed[1].parts[0], "tool_call_id", None) == "call-image-1"
    ), "Expected first tool call id to survive context processing unchanged"
    assert (
        getattr(processed[2].parts[0], "tool_call_id", None) == "call-image-1"
    ), "Expected first tool return id to survive context processing unchanged"
    assert (
        getattr(processed[3].parts[0], "tool_call_id", None) == "call-image-2"
    ), "Expected second tool call id to survive context processing unchanged"
    assert (
        getattr(processed[4].parts[0], "tool_call_id", None) == "call-image-2"
    ), "Expected second tool return id to survive context processing unchanged"
    assert (
        getattr(processed[0].parts[0], "content", None) == "Review these images."
    ), "Expected latest user prompt to remain unchanged during passthrough"
