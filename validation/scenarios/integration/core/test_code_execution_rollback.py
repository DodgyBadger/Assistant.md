"""Actual code tool mutations roll back after a genuine parent provider failure."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.code_tool_chat import (
    ROLLBACK_CODE,
    code_tool_provider,
    configure_provider,
)

pytestmark = pytest.mark.scenario


async def test_code_execution_mutation_rolls_back_with_failed_chat(
    app: Application,
) -> None:
    vault = app.vault("CodeExecutionRollbackVault")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        server.arguments = {"code": ROLLBACK_CODE}
        server.fail_after_tool = True
        server.rollback_path = vault / "notes/code-execution-write.md"
        session_id = "code_execution_rollback_session"
        checkpoint = app.events.checkpoint()
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Use code_execution then fail.",
                "session_id": session_id,
                "model": "code-tool-model",
            },
        )
        assert result["terminal_event"]["event"] == "error"
        assert result["task"]["status"] == "failed"
        task_id = result["task"]["task_id"]
        assert server.observed_mutation == "created inside code_execution\n"
        app.events.require(
            "execution_task_failed",
            after=checkpoint,
            task_id=task_id,
            kind="chat",
            scope=f"chat_session:{session_id}",
            status="failed",
        )
        app.events.require(
            "vault_mutation_recorded",
            after=checkpoint,
            task_id=task_id,
            path="notes/code-execution-write.md",
            operation="write",
        )
        app.events.require(
            "task_rollback_completed",
            after=checkpoint,
            task_id=task_id,
            terminal_status="failed",
        )
        assert not server.rollback_path.exists()
