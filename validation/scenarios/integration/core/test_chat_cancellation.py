"""Cancel a real chat after a tool mutation while the model response is gated."""

import asyncio
import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_retry_provider import retry_provider
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_cancel_chat_rolls_back_tool_write_and_retains_history(
    app: Application,
) -> None:
    vault = app.vault("ChatCancellationVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    with retry_provider() as server:
        server.mode = "tool_then_gate"
        await app.start()
        from core.chat.tool_history import analyze_tool_history
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        session = "chat_cancellation_session"
        prompt = "Cancel this chat."
        start = await app.request_async(
            "/api/chat/tasks",
            method="POST",
            data={
                "vault_name": vault.name,
                "prompt": prompt,
                "session_id": session,
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert start.status_code == 200, start.text
        task_id = start.json()["task"]["task_id"]
        assert await asyncio.to_thread(server.started.wait, 30)
        written = vault / "notes/cancelled-chat-write.md"
        assert written.exists()
        active = await app.request_async(f"/api/chat/sessions/{session}/active-task")
        assert (
            active.status_code == 200
            and active.json()["task_id"] == task_id
            and active.json()["status"] == "running"
        )
        checkpoint = app.events.checkpoint()
        cancelled = await app.request_async(
            f"/api/chat/sessions/{session}/cancel", method="POST"
        )
        assert cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
        assert (await app.wait_task(task_id))["status"] == "cancelled"
        assert not written.exists()
        app.events.require(
            "task_rollback_completed",
            after=checkpoint,
            task_id=task_id,
            terminal_status="cancelled",
        )
        assert (
            await app.request_async(f"/api/chat/sessions/{session}/active-task")
        ).status_code == 404
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        assert (
            len(detail.json()["messages"]) == 1
            and detail.json()["messages"][0]["role"] == "user"
            and prompt in detail.json()["messages"][0]["content"]
        )
        server.mode = "success"
        server.release.set()
        before = len(server.requests)
        follow = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Continue after cancellation.",
                "session_id": session,
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert follow["text"] and follow["terminal_event"]["event"] == "done"
        assert len(server.requests) > before
        assert prompt in json.dumps(server.requests[before]["messages"])
        history = get_runtime_context().chat_store.get_history(session, vault.name)
        assert analyze_tool_history(history or []).ok
        app.evidence.save_json("cancel-chat-provider-requests.json", server.requests)
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        rows = [
            row["data"]
            for row in activity.json()["entries"]
            if row.get("data", {}).get("session_id") == session
        ]
        assert any(
            row.get("event") == "chat_turn_cancelled" and row.get("task_id") == task_id
            for row in rows
        )
        assert any(
            row.get("event") == "chat_turn_completed"
            and row.get("task_id") == follow["task"]["task_id"]
            for row in rows
        )
