"""Real preflight failure retains actionable diagnostics and no transcript."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)

pytestmark = pytest.mark.scenario


async def test_chat_preflight_failure_activity_and_actionable_error(
    app: Application,
) -> None:
    vault = app.vault("ChatFailureVault")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        model = app.request(
            "/api/system/models/code-tool-model",
            method="PUT",
            data={
                "provider": "code-tool-provider",
                "model_string": "code-tool-model",
                "capabilities": ["text", "vision"],
            },
        )
        assert model.status_code == 200, model.text
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Attach a missing image.",
                "session_id": "chat_image_preflight_failure_session",
                "image_paths": ["missing-image.png"],
                "model": "code-tool-model",
            },
        )
        assert result["start_response"].status_code == 200
        assert result["terminal_event"]["event"] == "error"
        assert "Image file not found: missing-image.png" in result["text"]
        assert not (
            vault / "AssistantMD/Chat_Sessions/chat_image_preflight_failure_session.md"
        ).exists()
        response = app.request("/api/system/activity-log", params={"limit": 1000})
        assert response.status_code == 200
        entries = response.json()["entries"]
        assert any(
            row.get("message") == "Chat task request accepted"
            and row.get("data", {}).get("session_id")
            == "chat_image_preflight_failure_session"
            for row in entries
        )
        failures = [
            row
            for row in entries
            if row.get("message") == "Queued streaming chat preflight failed"
            and row.get("data", {}).get("session_id")
            == "chat_image_preflight_failure_session"
        ]
        assert failures
        data = failures[0]["data"]
        assert data["event"] == "chat_turn_failed" and data["status"] == "failed"
        assert data[
            "error_type"
        ] == "ValueError" and "Image file not found: missing-image.png" in json.dumps(
            data
        )
        assert server.calls == []
