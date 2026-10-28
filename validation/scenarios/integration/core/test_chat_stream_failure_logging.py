"""Provider failure and follow-up recovery through real streaming chat."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_stream_provider import streaming_provider
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.model_failures import (
    PRIVATE_VALUES,
    rejecting_provider,
)
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_stream_failure_marker_logging_and_recovery(app: Application) -> None:
    vault = app.vault("ChatStreamFailureVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    app.evidence.register_secret(*PRIVATE_VALUES)
    with (
        rejecting_provider() as (failure_server, requests),
        streaming_provider() as success_server,
    ):
        app.evidence.register_secret(
            f"http://127.0.0.1:{failure_server.server_port}/v1"
        )
        success_server.release.set()
        await app.start()
        for name, value in (
            (
                "FAILURE_PROVIDER_URL",
                f"http://127.0.0.1:{failure_server.server_port}/v1",
            ),
            ("FAILURE_PROVIDER_KEY", "validation-provider-key"),
        ):
            assert (
                app.request(
                    "/api/system/secrets",
                    method="PUT",
                    data={"name": name, "value": value},
                ).status_code
                == 200
            )
        assert (
            app.request(
                "/api/system/providers/failure-provider",
                method="PUT",
                data={
                    "base_url": "FAILURE_PROVIDER_URL",
                    "api_key": "FAILURE_PROVIDER_KEY",
                },
            ).status_code
            == 200
        )
        assert (
            app.request(
                "/api/system/models/failure-model",
                method="PUT",
                data={
                    "provider": "failure-provider",
                    "model_string": "local-model",
                    "capabilities": ["text"],
                },
            ).status_code
            == 200
        )
        configure_provider(app, success_server)
        session = "chat_stream_failure_session"
        prompt = "Trigger the streaming provider failure."
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": prompt,
                "session_id": session,
                "model": "failure-model",
                "context_template": "full_history.md",
            },
        )
        assert (
            result["start_response"].status_code == 200
            and result["terminal_event"]["event"] == "error"
        )
        assert requests
        assert not (vault / f"AssistantMD/Chat_Sessions/{session}.md").exists()
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        payload = detail.json()
        assert (
            len(payload["messages"]) == 1 and payload["messages"][0]["role"] == "user"
        )
        failure = payload["latest_failure"]
        assert (
            failure["status"] == "failed"
            and failure["phase"] == "agent_stream"
            and failure["streaming"] is True
        )
        assert (
            failure["error_type"] == "ModelHTTPError"
            and failure["accepted_user_sequence_index"] == 0
        )
        follow = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Continue after the streaming failure.",
                "session_id": session,
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert follow["text"] and follow["terminal_event"]["event"] == "done"
        assert success_server.requests
        app.evidence.save_json(
            "recovery-provider-requests.json", success_server.requests
        )
        messages = success_server.requests[0]["messages"]
        assert prompt in json.dumps(messages)
        assert any(
            message.get("role") == "system"
            and failure["phase"] in json.dumps(message.get("content"))
            and failure["error_type"] in json.dumps(message.get("content"))
            for message in messages
        )
        recovered = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert (
            recovered.status_code == 200 and recovered.json()["latest_failure"] is None
        )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        entries = activity.json()["entries"]
        assert any(
            row.get("message") == "Streaming chat execution started"
            and row.get("data", {}).get("session_id") == session
            for row in entries
        )
        failures = [
            row
            for row in entries
            if row.get("message") == "Streaming chat execution failed"
            and row.get("data", {}).get("session_id") == session
        ]
        assert failures
        data = failures[0]["data"]
        assert data["event"] == "chat_turn_failed" and data["status"] == "failed"
        assert (
            data["tool_call_count"] == 0
            and data["task_id"] == result["task"]["task_id"]
        )
        assert data["error_type"] == "ModelHTTPError"
        assert any(
            row.get("data", {}).get("event") == "chat_turn_failure_marker_recorded"
            and row["data"].get("session_id") == session
            for row in entries
        )
        for artifact in (
            json.dumps(result["terminal_event"]),
            detail.text,
            activity.text,
        ):
            assert all(private not in artifact for private in PRIVATE_VALUES)
