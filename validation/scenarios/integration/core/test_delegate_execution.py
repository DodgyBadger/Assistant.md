"""Delegate executes real streamed child agents and preserves task audit output."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.delegate_inputs import (
    delegate_provider,
    invoke_delegate,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "tools, stripped",
    [
        (None, []),
        (
            ["FILE_READ", "Delegate", "Code_Execution", "JOB"],
            ["code_execution", "delegate", "job"],
        ),
        (["file_read"], []),
    ],
)
async def test_delegate_real_chat_streams_child_and_filters_tools(
    app: Application, tools, stripped
):
    vault = app.vault("DelegateToolVault")
    app.write(vault, "notes/0.md", "DELEGATE_CONTENT")
    await app.start()
    from core.runtime.state import get_runtime_context

    with delegate_provider(app) as server:
        server.arguments = {
            "prompt": "Read child input and return result",
            "model": "code-tool-model",
        }
        if tools is not None:
            server.arguments["tools"] = tools
        turn = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "delegate_real_chat",
                "prompt": "Delegate the bounded child task",
                "model": "code-tool-model",
            },
        )
        assert turn["terminal_event"]["event"] == "done"
        assert "failed:" not in turn["text"].lower()
        app.events.require(
            "delegate_started",
            **{
                "session_id": "delegate_real_chat",
                "model": "code-tool-model",
                "model_source": "explicit",
                "stripped_tools": stripped,
                "detached_from_parent_lifecycle": False,
            },
        )
        app.events.require(
            "delegate_completed",
            **{"session_id": "delegate_real_chat", "status": "completed"},
        )
        activity = app.request(
            "/api/system/activity-log?limit=100&tag=delegate-tool&search=delegate_real_chat"
        )
        assert activity.status_code == 200
        assert any(
            row.get("data", {}).get("event") == "delegate_completed"
            for row in activity.json()["entries"]
        )
        if tools:
            app.events.require(
                "delegate_tool_binding_resolved", **{"requested": ["file_read"]}
            )
            jobs = await get_runtime_context().task_coordinator.list_tasks(
                kind="delegate"
            )
            child = next(
                job
                for job in reversed(jobs)
                if job.metadata.get("session_id") == "delegate_real_chat"
            )
            assert [row["tool"] for row in child.metadata["recent_activity"]] == [
                "file_read"
            ]
            counts = child.metadata["tool_call_counts"]
            assert counts.get("completed", 0) + counts.get("failed", 0) == 1


async def test_delegate_real_child_can_complete_forty_tool_calls(app: Application):
    vault = app.vault("DelegateFortyCalls")
    for index in range(40):
        app.write(vault, f"notes/{index}.md", f"Real source {index}")
    await app.start()
    with delegate_provider(app) as server:
        server.child_call_count = 40
        result = await invoke_delegate(
            vault,
            "delegate_many_tool_calls",
            {
                "prompt": "Read the bounded input files",
                "model": "code-tool-model",
                "tools": ["file_read"],
            },
        )
        assert result.return_value == "DELEGATE_LOCAL_COMPLETE"
        audit = result.metadata["audit"]
        assert audit["tool_call_count"] == 40
        assert audit["tool_calls"][0]["tool"] == "file_read"
        assert "result" in audit["tool_calls"][0]
        assert any(row.get("stream") is True for row in server.calls)


async def test_delegate_omitted_child_model_inherits_real_parent_model(
    app: Application,
):
    vault = app.vault("DelegateParentModel")
    await app.start()
    with delegate_provider(app) as server:
        server.arguments = {"prompt": "Return child output without tools"}
        turn = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "delegate_inherit",
                "prompt": "Delegate a task",
                "model": "code-tool-model",
            },
        )
        assert turn["terminal_event"]["event"] == "done"
        app.events.require(
            "delegate_started",
            **{
                "session_id": "delegate_inherit",
                "model": "code-tool-model",
                "model_source": "parent",
            },
        )
