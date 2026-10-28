"""Retry a genuinely interrupted provider stream without duplicating history."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_retry_provider import retry_provider
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_manual_retry_interrupted_chat(app: Application) -> None:
    vault = app.vault("ChatManualRetryVault")
    with retry_provider() as server:
        server.mode = "disconnect"
        await app.start()
        configure_provider(app, server)
        assert (
            app.request(
                "/api/system/settings/general/model_stream_retries",
                method="PUT",
                data={"value": "0"},
            ).status_code
            == 200
        )
        session = "chat_manual_retry_session"
        prompt = "Retry this interrupted request."
        failed = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": prompt,
                "session_id": session,
                "model": "session-ops-model",
            },
        )
        assert failed["terminal_event"]["event"] == "error"
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        failure = detail.json()["latest_failure"]
        assert failure["failure_kind"] == "transient_network"
        assert failure["retryable"] is True
        server.mode = "success"
        before = len(server.requests)
        retry = await app.request_async(
            f"/api/chat/sessions/{session}/retry",
            method="POST",
            data={"vault_name": vault.name},
        )
        assert retry.status_code == 200, retry.text
        task_id = retry.json()["task"]["task_id"]
        assert (await app.wait_task(task_id))["status"] == "completed"
        replay = await app.request_async(f"/api/chat/tasks/{task_id}/events")
        assert (
            replay.status_code == 200
            and '"event": "done"' in replay.text
            and "recovered primary response" in replay.text
        )
        assert len(server.requests) == before + 1
        messages = server.requests[-1]["messages"]
        assert (
            sum(
                row.get("role") == "user" and prompt in json.dumps(row.get("content"))
                for row in messages
            )
            == 1
        )
        assert any(
            row.get("role") == "system"
            and failure["phase"] in json.dumps(row.get("content"))
            and failure["error_type"] in json.dumps(row.get("content"))
            for row in messages
        )
        app.evidence.save_json("manual-retry-provider-requests.json", server.requests)
        restored = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert restored.status_code == 200 and restored.json()["latest_failure"] is None
        rows = restored.json()["messages"]
        assert sum(row["role"] == "user" for row in rows) == 1
        assert sum(row["role"] == "assistant" for row in rows) == 1
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        assert any(
            row.get("data", {}).get("event") == "chat_manual_retry_started"
            and row["data"].get("session_id") == session
            for row in activity.json()["entries"]
        )
