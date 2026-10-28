"""Real Gmail 404s settle as failed tool results while actual chat transport completes."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.scenarios.support.gmail_http import configure_gmail, gmail_server

pytestmark = pytest.mark.scenario


async def test_gmail_not_found_tool_result_preserves_guidance_and_chat_continues(
    app: Application,
) -> None:
    vault = app.vault("GmailToolFailureChatVault")
    await app.start()
    from pydantic_ai.messages import ToolReturnPart

    from core.runtime.state import get_runtime_context

    with gmail_server(app) as gmail, code_tool_provider() as model:
        configure_provider(app, model)
        configure_gmail(app, gmail)
        model.tool_name = "gmail"
        model.arguments = {
            "operation": "get_message",
            "message_id": "stale-draft-message",
        }
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Inspect the draft message.",
                "session_id": "gmail_tool_failure_chat",
                "model": "code-tool-model",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        assert result["task"]["status"] == "completed"
        assert "Do not retry the same resource ID" in result["text"]
        assert "Search Gmail again" in result["text"]
        returns = [
            message
            for message in model.calls[-1]["messages"]
            if message["role"] == "tool"
        ]
        assert returns
        assert "Do not retry the same resource ID" in returns[-1]["content"]
        assert "Search Gmail again" in returns[-1]["content"]
        native = get_runtime_context().chat_store.get_history(
            "gmail_tool_failure_chat", vault.name
        )
        tool_returns = [
            part
            for message in native
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        assert tool_returns[-1].metadata["failure_kind"] == "not_found"
        finished = next(
            event
            for event in result["events"]
            if event.get("event") == "tool_call_finished"
            and event.get("tool_name") == "gmail"
        )
        assert finished["terminal_state"] == "failed"
        assert all(
            key not in finished for key in ("result", "result_metadata", "artifact_ref")
        )
        events = get_runtime_context().chat_store.get_tool_events(
            "gmail_tool_failure_chat", vault.name
        )
        persisted = [
            event
            for event in events
            if event.event_type == "result" and event.tool_name == "gmail"
        ]
        assert len(persisted) == 1
        assert (
            json.loads(persisted[0].result_metadata_json)["failure_kind"] == "not_found"
        )
        app.events.require(
            "chat_tool_call_finished", tool_name="gmail", terminal_state="failed"
        )
