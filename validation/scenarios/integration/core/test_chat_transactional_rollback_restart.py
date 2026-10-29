"""Configured transactional tool HTTP failures require real rollback before replay."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_failure_recovery_contracts import set_setting
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.recovery_activity import require_recovery_activity
from validation.scenarios.support.rollback_restart_workload import (
    CHANGED,
    ORIGINAL,
    chat_with_redirects,
    rollback_restart_server,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("case", ["recover", "budget_exhausted", "rollback_disabled"])
async def test_transactional_http_failure_rolls_back_before_restart_or_refuses(
    app: Application, case
):
    vault = app.vault("TransactionalRollbackRestart")
    app.write(vault, "notes/transactional.md", ORIGINAL)
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    from pydantic_ai.messages import ToolCallPart, ToolReturnPart, UserPromptPart

    from core.chat.tool_history import analyze_tool_history
    from core.runtime.state import get_runtime_context

    for name, value in (
        ("default_model", "test"),
        ("compaction_type", "none"),
        ("model_stream_retries", "1"),
        ("model_stream_retry_base_delay_seconds", "0"),
        ("model_stream_retry_max_delay_seconds", "0"),
        ("task_rollback_enabled", "false" if case == "rollback_disabled" else "true"),
    ):
        set_setting(app, name, value)
    target = vault / "notes/transactional.md"
    with rollback_restart_server(
        app, target, always_fail=case == "budget_exhausted"
    ) as server:
        result = await chat_with_redirects(
            app,
            {
                "vault_name": vault.name,
                "session_id": "transactional-restart",
                "prompt": "Run the configured transactional workload once and report the result.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        redirects = [
            event["data"]
            for event in result["events"]
            if event["event"] == "chat_retry_redirect"
        ]
        if case == "rollback_disabled":
            assert result["terminal"]["event"] == "error"
            assert len(result["task_ids"]) == 1 and not redirects
            assert len(server.calls) == len(server.effects) == 1
            assert target.read_text() == CHANGED
            require_recovery_activity(
                app,
                "chat_recovery_abandoned",
                reason="rollback_or_restart_unavailable",
                task_id=result["task_ids"][0],
            )
        else:
            assert len(redirects) == 1 and len(result["task_ids"]) == 2
            redirect = redirects[0]
            assert redirect["strategy"] == "terminal_rollback_restart"
            assert redirect["rollback_status"] in {"completed", "already_rolled_back"}
            assert redirect["attempt"] == 1 and redirect["max_attempts"] == 2
            assert [row["file_before_request"] for row in server.calls[:2]] == [
                ORIGINAL,
                ORIGINAL,
            ]
            assert server.effects == [CHANGED, CHANGED]
            if case == "recover":
                assert result["terminal"]["event"] == "done"
                assert [task["status"] for task in result["tasks"]] == [
                    "failed",
                    "completed",
                ]
                assert len(server.calls) == 3
                assert target.read_text() == CHANGED
            else:
                assert result["terminal"]["event"] == "error"
                assert [task["status"] for task in result["tasks"]] == [
                    "failed",
                    "failed",
                ]
                assert len(server.calls) == 2
                assert target.read_text() == ORIGINAL
                require_recovery_activity(
                    app,
                    "chat_recovery_rejected",
                    reason="retry_budget_exhausted",
                    task_id=result["task_ids"][-1],
                )
        store = get_runtime_context().chat_store
        history = (
            store.get_history("transactional-restart", vault.name, mode="raw") or []
        )
        parts = [part for message in history for part in message.parts]
        assert len([part for part in parts if isinstance(part, UserPromptPart)]) == 1
        assert analyze_tool_history(history).ok
        if case == "recover":
            assert (
                len(
                    [
                        part
                        for part in parts
                        if isinstance(part, ToolCallPart)
                        and part.tool_name == "rollback_restart_workload"
                    ]
                )
                == 1
            )
            assert (
                len(
                    [
                        part
                        for part in parts
                        if isinstance(part, ToolReturnPart)
                        and part.tool_name == "rollback_restart_workload"
                    ]
                )
                == 1
            )
        app.evidence.save_json(
            "transactional-restart-final.json",
            {
                "case": case,
                "file": target.read_text(),
                "task_ids": result["task_ids"],
                "redirects": redirects,
                "canonical_message_count": len(history),
            },
        )
