"""A partial real provider stream retries safely or fails when retries are disabled."""

import json

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.pytest_scenarios.support.chat_retry_provider import retry_provider
from validation.pytest_scenarios.support.chat_tasks import chat_turn
from validation.pytest_scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_partial_stream_retry_and_disable(app: Application) -> None:
    vault = app.vault("ChatStreamAutoRetryVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    with retry_provider() as server:
        await app.start()
        configure_provider(app, server)
        metadata = app.request("/api/metadata")
        assert metadata.status_code == 200
        disabled_tools = [row["name"] for row in metadata.json()["tools"]]
        disabled = app.request(
            "/api/system/settings/general/disabled_tools",
            method="PUT",
            data={"value": json.dumps(disabled_tools)},
        )
        assert disabled.status_code == 200, disabled.text
        assert (
            app.request(
                "/api/system/settings/general/model_stream_retries",
                method="PUT",
                data={"value": "1"},
            ).status_code
            == 200
        )
        assert (
            app.request(
                "/api/system/settings/general/model_stream_retry_base_delay_seconds",
                method="PUT",
                data={"value": "0"},
            ).status_code
            == 200
        )
        server.mode = "fail_once"
        recovered = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Retry the interrupted stream.",
                "session_id": "primary_auto_retry",
                "context_template": "full_history.md",
                "model": "session-ops-model",
            },
        )
        assert recovered["terminal_event"]["event"] == "done"
        assert recovered["task"]["metadata"]["tools"] == []
        assert len(server.requests) == 2
        retries = [
            event
            for event in recovered["events"]
            if event["event"] == "chat_retry_scheduled"
        ]
        assert len(retries) == 1
        retry = retries[0]
        assert (
            retry["reset_response"] is True
            and retry["replay_scope"] == "no_chat_tools"
            and retry["status"] == "scheduled"
        )
        assert retry["vault_name"] == vault.name and retry["error"]
        assert retry["issue"] == f"chat_retry:{recovered['task']['task_id']}:1"
        snapshot = app.request(
            f"/api/chat/tasks/{recovered['task']['task_id']}/replay-snapshot"
        )
        assert snapshot.status_code == 200
        deltas = [
            event for event in snapshot.json()["events"] if event["event"] == "delta"
        ]
        assert len(deltas) == 1
        text = deltas[0]["choices"][0]["delta"]["content"]
        assert (
            text == "recovered primary response"
            and "discarded partial response" not in text
        )
        detail = app.request(
            "/api/chat/sessions/primary_auto_retry", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        messages = detail.json()["messages"]
        assert sum(row["role"] == "user" for row in messages) == 1
        assert sum(row["role"] == "assistant" for row in messages) == 1
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        assert any(
            row.get("data", {}).get("event") == "chat_retry_scheduled"
            and row["data"].get("session_id") == "primary_auto_retry"
            for row in activity.json()["entries"]
        )
        assert (
            app.request(
                "/api/system/settings/general/model_stream_retries",
                method="PUT",
                data={"value": "0"},
            ).status_code
            == 200
        )
        server.mode = "disconnect"
        before = len(server.requests)
        disabled = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Do not retry the interrupted stream.",
                "session_id": "primary_retry_disabled",
                "context_template": "full_history.md",
                "model": "session-ops-model",
            },
        )
        assert disabled["terminal_event"]["event"] == "error"
        assert len(server.requests) == before + 1
        assert not any(
            event["event"] == "chat_retry_scheduled" for event in disabled["events"]
        )
        app.evidence.save_json(
            "automatic-retry-provider-requests.json", server.requests
        )
