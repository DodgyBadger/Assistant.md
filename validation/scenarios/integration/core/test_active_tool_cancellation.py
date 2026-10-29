"""Cancellation interrupts active tool work and settles reviewed tool history."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.active_tool_cancellation import (
    active_provider,
    hold_vault_file_lock,
    wait_active_tool,
)
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.inline_edit_reviews import (
    request_review,
    submit_review,
)

pytestmark = pytest.mark.scenario


async def test_cancel_active_code_tool_after_mutation(app: Application) -> None:
    vault = app.vault("ActiveToolCancellationVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    from core.chat.tool_history import analyze_tool_history
    from core.runtime.state import get_runtime_context

    with active_provider(app) as server:
        session = "active-code-cancel"
        response = await app.request_async(
            "/api/chat/tasks",
            method="POST",
            data={
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Execute the controlled operation.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["task"]["task_id"]
        assert await asyncio.to_thread(server.entered.wait, 30)
        written = vault / "notes/active-effect.md"
        assert written.read_text() == "ACTIVE_EFFECT"
        await wait_active_tool(app, task_id, "code_execution")
        checkpoint = app.events.checkpoint()
        cancelled = await app.request_async(
            f"/api/tasks/{task_id}/cancel", method="POST"
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
        server.mode = "success"
        server.release.set()
        follow = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Continue after cancellation.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert follow["terminal_event"]["event"] == "done"
        assert not written.exists()
        assert analyze_tool_history(
            get_runtime_context().chat_store.get_history(session, vault.name) or []
        ).ok


async def test_cancel_approved_active_write_settles_review(app: Application) -> None:
    vault = app.vault("ActiveReviewCancellationVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    from pydantic_ai.messages import ToolReturnPart

    from core.chat.tool_history import analyze_tool_history
    from core.runtime.state import get_runtime_context

    with active_provider(app) as server:
        server.mode = "review"
        session = "active-review-cancel"
        path = vault / "reviewed.md"
        review = await request_review(
            app,
            server,
            vault.name,
            session,
            {"operation": "write", "path": "reviewed.md", "content": "REVIEWED_EFFECT"},
        )
        assert not path.exists()
        with hold_vault_file_lock(vault, path) as release:
            submitted = await submit_review(app, review, {})
            assert submitted.status_code == 200, submitted.text
            task_id = submitted.json()["task"]["task_id"]
            await wait_active_tool(app, task_id, "file_write")
            cancelled = await app.request_async(
                f"/api/tasks/{task_id}/cancel", method="POST"
            )
            assert (
                cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
            )
            release.set()
        assert (await app.wait_task(task_id))["status"] == "cancelled"
        assert not path.exists()
        detail = app.request(
            f"/api/vaults/{vault.name}/chat/{session}/deferred-reviews/{review['artifact_ref']}"
        )
        assert detail.status_code == 200 and detail.json()["status"] == "cancelled"
        history = (
            get_runtime_context().chat_store.get_history(session, vault.name) or []
        )
        assert analyze_tool_history(history).ok
        returns = [
            part
            for message in history
            for part in message.parts
            if isinstance(part, ToolReturnPart)
            and part.tool_call_id == review["approvals"][0]["tool_call_id"]
        ]
        assert len(returns) == 1 and returns[0].outcome == "interrupted"
        server.mode = "success"
        follow = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Continue after cancellation.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert follow["terminal_event"]["event"] == "done" and not path.exists()
        assert analyze_tool_history(
            get_runtime_context().chat_store.get_history(session, vault.name) or []
        ).ok
