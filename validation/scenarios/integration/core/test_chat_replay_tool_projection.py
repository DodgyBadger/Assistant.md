"""Real reasoning and tool events have safe compact replay projections."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.replay_projection_provider import projection_provider

pytestmark = pytest.mark.scenario


async def test_replay_projects_real_reasoning_and_tool_without_arguments(
    app: Application,
) -> None:
    vault = app.vault("ChatReplayProjectionVault")
    app.write(vault, "notes/private.md", "PRIVATE_TOOL_RESULT_CONTENT")
    app.evidence.register_secret("PRIVATE_TOOL_RESULT_CONTENT")
    await app.start()
    with projection_provider(app) as server:
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "replay-projection",
                "prompt": "Read the note.",
                "model": "code-tool-model",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        assert len(server.requests) == 2
        snapshot = await app.request_async(
            f"/api/chat/tasks/{result['task']['task_id']}/replay-snapshot"
        )
        assert snapshot.status_code == 200 and snapshot.json()["available"] is True
        events = snapshot.json()["events"]
        reasoning = [row for row in events if row["event"] == "thinking_delta"]
        assert (
            len(reasoning) == 1
            and reasoning[0]["delta"]["content"] == "thinking start thinking end"
        )
        started = [row for row in events if row["event"] == "tool_call_started"]
        finished = [row for row in events if row["event"] == "tool_call_finished"]
        assert len(started) == len(finished) == 1
        assert started[0]["tool_name"] == finished[0]["tool_name"] == "file_read"
        assert (
            started[0]["tool_call_id"]
            == finished[0]["tool_call_id"]
            == "read-projection"
        )
        assert all("arguments" not in row and "result" not in row for row in events)
        assert "PRIVATE_TOOL_RESULT_CONTENT" not in json.dumps(events)
        assert events[-1]["event"] == "done"
