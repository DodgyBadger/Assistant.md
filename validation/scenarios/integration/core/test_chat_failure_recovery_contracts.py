"""Real idle, active-tool and preflight failure boundaries remain recoverable."""

import asyncio
import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.active_tool_cancellation import (
    active_provider,
    wait_active_tool,
)
from validation.scenarios.support.chat_failure_recovery_contracts import (
    restore_history,
    seed_corrupt_history,
    set_setting,
)
from validation.scenarios.support.chat_idle_provider import idle_provider
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_idle_model_stream_times_out_and_next_turn_succeeds(
    app: Application,
) -> None:
    vault = app.vault("ChatRealIdleTimeoutVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    with idle_provider() as server:
        await app.start()
        configure_provider(app, server)
        set_setting(app, "model_stream_idle_timeout_seconds", "3")
        set_setting(app, "model_stream_retries", "0")
        checkpoint = app.events.checkpoint()
        failed = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "idle-timeout",
                "prompt": "Wait for the model.",
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert server.started.is_set() and len(server.requests) == 1
        assert failed["terminal_event"]["event"] == "error"
        assert (
            failed["terminal_event"]["details"]["failure_kind"]
            == "model_stream_idle_timeout"
        )
        assert failed["task"]["metadata"]["model_stream_state"] == "waiting_for_model"
        app.events.require(
            "model_stream_idle_timed_out",
            after=checkpoint,
            task_id=failed["task"]["task_id"],
            session_id="idle-timeout",
            status="timed_out",
            attempt=1,
            active_tool_count=0,
            error_type="ModelStreamIdleTimeout",
        )
        server.release.set()
        follow = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "idle-timeout",
                "prompt": "Continue after timeout.",
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert follow["terminal_event"]["event"] == "done"
        detail = app.request(
            "/api/chat/sessions/idle-timeout", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200 and detail.json()["latest_failure"] is None


async def test_active_tool_is_exempt_from_model_idle_timeout(app: Application) -> None:
    vault = app.vault("ChatActiveToolIdleVault")
    await app.start()
    with active_provider(app) as server:
        set_setting(app, "model_stream_idle_timeout_seconds", "3")
        set_setting(app, "model_stream_retries", "0")
        response = await app.request_async(
            "/api/chat/tasks",
            method="POST",
            data={
                "vault_name": vault.name,
                "session_id": "active-tool-idle",
                "prompt": "Execute the controlled operation.",
                "model": "code-tool-model",
            },
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["task"]["task_id"]
        assert await asyncio.to_thread(server.entered.wait, 30)
        assert (vault / "notes/active-effect.md").exists()
        await wait_active_tool(app, task_id, "code_execution")
        await asyncio.sleep(4.0)
        active = await app.request_async(f"/api/tasks/{task_id}")
        assert active.status_code == 200 and active.json()["status"] == "running"
        assert active.json()["metadata"]["model_stream_state"] == "tool_running"
        assert "code_execution" in active.json()["metadata"]["active_tools"]
        server.mode = "success"
        server.release.set()
        assert (await app.wait_task(task_id))["status"] == "completed"
        assert (vault / "notes/active-effect.md").read_text() == "ACTIVE_EFFECT"


async def test_corrupt_history_preflight_failure_then_repaired_turn(
    app: Application,
) -> None:
    vault = app.vault("ChatPreflightCorruptionVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    with idle_provider() as server:
        server.release.set()
        configure_provider(app, server)
        session = "preflight-corrupt"
        original = seed_corrupt_history(app, vault.name, session)
        checkpoint = app.events.checkpoint()
        failed = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Run with corrupt history.",
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert (
            failed["terminal_event"]["event"] == "error"
            and failed["task"]["status"] == "failed"
        )
        assert not server.requests
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        rows = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("event") == "chat_turn_failed"
            and row["data"].get("session_id") == session
            and row["data"].get("phase") == "preflight"
        ]
        assert rows and rows[0]["status"] == "failed"
        assert "Earlier durable request." not in json.dumps(rows)
        restore_history(app, vault.name, session, original)
        follow = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Continue after repair.",
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert follow["terminal_event"]["event"] == "done"
        assert server.requests
        assert any(
            row["data"].get("session_id") == session
            for row in app.events.select(after=checkpoint)
        )
