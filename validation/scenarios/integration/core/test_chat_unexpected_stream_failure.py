"""Malformed streamed provider data fails safely and allows a later turn."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_malformed_stream import malformed_stream_provider
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn

pytestmark = pytest.mark.scenario


async def test_malformed_provider_stream_failure_and_recovery(app: Application) -> None:
    vault = app.vault("MalformedChatStreamVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    with malformed_stream_provider(app) as server:
        session = "malformed-stream"
        failed = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Send malformed stream.",
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert (
            failed["terminal_event"]["event"] == "error"
            and failed["task"]["status"] == "failed"
        )
        assert len(server.requests) == 1
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        marker = detail.json()["latest_failure"]
        assert (
            marker is not None
            and marker["phase"] == "agent_stream"
            and marker["status"] == "failed"
        )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        rows = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("event") == "chat_turn_failed"
            and row["data"].get("session_id") == session
        ]
        assert rows and rows[0]["task_id"] == failed["task"]["task_id"]
        assert (
            "private-sse-marker" not in json.dumps(rows)
            and "private-sse-marker" not in detail.text
            and "private-sse-marker" not in json.dumps(failed["terminal_event"])
        )
        follow = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Continue after failure.",
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert follow["terminal_event"]["event"] == "done" and len(server.requests) == 2
        recovered = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert (
            recovered.status_code == 200 and recovered.json()["latest_failure"] is None
        )
