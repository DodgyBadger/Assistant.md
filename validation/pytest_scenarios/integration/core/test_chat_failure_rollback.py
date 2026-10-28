"""Actual file-write tool effects roll back when a later model request is rejected."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_tasks import chat_turn
from validation.pytest_scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)

pytestmark = pytest.mark.scenario


async def test_failed_chat_rolls_back_real_file_write(app: Application) -> None:
    vault = app.vault("ChatFailureRollbackVault")
    await app.start()
    with code_tool_provider() as provider:
        configure_provider(app, provider)
        provider.tool_name = "file_write"
        provider.arguments = {
            "operation": "write",
            "path": "notes/failed-chat-write.md",
            "content": "created before chat failure\n",
        }
        provider.fail_after_tool = True
        provider.rollback_path = vault / "notes/failed-chat-write.md"
        checkpoint = app.events.checkpoint()
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Fail after mutating a file.",
                "session_id": "chat_failure_rollback_session",
                "model": "code-tool-model",
            },
        )
        assert result["terminal_event"]["event"] == "error"
        assert result["task"]["status"] == "failed"
        assert provider.observed_mutation == "created before chat failure\n"
        task_id = result["task"]["task_id"]
        app.events.require(
            "execution_task_failed",
            after=checkpoint,
            kind="chat",
            scope="chat_session:chat_failure_rollback_session",
            status="failed",
            task_id=task_id,
        )
        app.events.require(
            "vault_mutation_recorded",
            after=checkpoint,
            task_id=task_id,
            path="notes/failed-chat-write.md",
            operation="write",
        )
        app.events.require(
            "task_rollback_completed",
            after=checkpoint,
            task_id=task_id,
            terminal_status="failed",
        )
        assert not (vault / "notes/failed-chat-write.md").exists()
