"""A real interruption resumes after settled read-only tool calls."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_checkpoint_retry_provider import (
    checkpoint_retry_provider,
)
from validation.scenarios.support.chat_failure_recovery_contracts import set_setting
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn

pytestmark = pytest.mark.scenario


async def test_checkpoint_retry_does_not_replay_completed_reads(
    app: Application,
) -> None:
    vault = app.vault("ChatCheckpointRetryVault")
    app.write(vault, "notes/1.md", "FIRST_READ")
    app.write(vault, "notes/2.md", "SECOND_READ")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart

    from core.runtime.state import get_runtime_context

    with checkpoint_retry_provider(app) as server:
        set_setting(app, "model_stream_retries", "1")
        set_setting(app, "model_stream_retry_base_delay_seconds", "0")
        checkpoint = app.events.checkpoint()
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "checkpoint-retry",
                "prompt": "Read both notes.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        assert len(server.requests) == 4
        retries = [
            event
            for event in result["events"]
            if event["event"] == "chat_retry_scheduled"
        ]
        selections = [
            event
            for event in result["events"]
            if event["event"] == "chat_recovery_checkpoint_selected"
        ]
        assert len(retries) == len(selections) == 1
        assert (
            retries[0]["strategy"] == "resume_snapshot"
            and retries[0]["trimmed_failed_response"] is True
        )
        tool_events = [
            row
            for row in app.events.select(after=checkpoint)
            if row["name"] == "tool_invoked" and row["data"].get("tool") == "file_read"
        ]
        assert len(tool_events) == 2
        history = (
            get_runtime_context().chat_store.get_history("checkpoint-retry", vault.name)
            or []
        )
        parts = [part for message in history for part in message.parts]
        assert [
            part.tool_call_id
            for part in parts
            if isinstance(part, ToolCallPart) and part.tool_name == "file_read"
        ] == ["read-1", "read-2"]
        assert [
            part.tool_call_id
            for part in parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "file_read"
        ] == ["read-1", "read-2"]
        assert (
            sum(
                isinstance(part, ToolCallPart) and part.tool_name == "file_read"
                for part in parts
            )
            == 2
        )
        assert (
            sum(
                isinstance(part, ToolReturnPart) and part.tool_name == "file_read"
                for part in parts
            )
            == 2
        )
        assert not any(
            isinstance(part, TextPart) and "discarded" in part.content for part in parts
        )
        snapshot = app.request(
            f"/api/chat/tasks/{result['task']['task_id']}/replay-snapshot"
        )
        assert snapshot.status_code == 200 and "discarded partial response" not in str(
            snapshot.json()["events"]
        )
