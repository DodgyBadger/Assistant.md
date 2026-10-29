"""Tool-initiated compaction handles no-op, failure, and cancellation."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.scenarios.support.compaction_journeys import configure_compaction
from validation.scenarios.support.gated_compaction_author import gated_author
from validation.scenarios.support.model_failures import (
    PRIVATE_VALUES,
    rejecting_provider,
)
from validation.scenarios.support.tool_compaction_outcomes import (
    compaction_results,
    seed_compactable,
)

pytestmark = pytest.mark.scenario


async def test_tool_compaction_noop_preserves_history(app: Application) -> None:
    vault = app.vault("ToolCompactionNoopVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    from core.runtime.state import get_runtime_context

    with gated_author() as author, code_tool_provider() as model:
        configure_compaction(app, author)
        configure_provider(app, model)
        model.tool_name = "chat_history_compact"
        model.arguments = {"operation": "compact"}
        session = "compaction-noop"
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Compact this short session.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert result["terminal_event"]["event"] == "done"
        store = get_runtime_context().chat_store
        output = compaction_results(store, vault.name, session)
        assert (
            len(output) == 1
            and output[0]["status"] == "unavailable"
            and output[0]["checkpoint_id"] is None
        )
        assert store.get_latest_context_checkpoint(session, vault.name) is None
        assert not author.requests
        assert store.get_message_count(session, vault.name, mode="raw") >= 2


async def test_tool_compaction_provider_failure_preserves_history(
    app: Application,
) -> None:
    vault = app.vault("ToolCompactionFailureVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    app.evidence.register_secret(*PRIVATE_VALUES)
    await app.start()
    from core.runtime.execution_tasks import ExecutionTaskKind
    from core.runtime.state import get_runtime_context

    with rejecting_provider() as (author, requests), code_tool_provider() as model:
        configure_compaction(app, author)
        configure_provider(app, model)
        model.tool_name = "chat_history_compact"
        model.arguments = {"operation": "compact"}
        session = "compaction-failure"
        store = get_runtime_context().chat_store
        seed_compactable(store, vault.name, session)
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Compact this session.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert requests
        assert (
            result["terminal_event"]["event"] == "error"
            and result["task"]["status"] == "failed"
        )
        assert store.get_latest_context_checkpoint(session, vault.name) is None
        assert store.get_message_count(session, vault.name, mode="raw") >= 5
        tasks = await get_runtime_context().task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert (
            len(tasks) == 1
            and tasks[0].status == "failed"
            and tasks[0].parent_task_id == result["task"]["task_id"]
        )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        assert all(
            private not in activity.text
            and private not in str(result["terminal_event"])
            for private in PRIVATE_VALUES
        )


async def test_tool_compaction_cancellation_preserves_history(app: Application) -> None:
    vault = app.vault("ToolCompactionCancelVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    from core.runtime.execution_tasks import ExecutionTaskKind
    from core.runtime.state import get_runtime_context

    with gated_author() as author, code_tool_provider() as model:
        configure_compaction(app, author)
        configure_provider(app, model)
        model.tool_name = "chat_history_compact"
        model.arguments = {"operation": "compact"}
        session = "compaction-cancel"
        store = get_runtime_context().chat_store
        seed_compactable(store, vault.name, session)
        started = await app.request_async(
            "/api/chat/tasks",
            method="POST",
            data={
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Compact this session.",
                "model": "code-tool-model",
                "context_template": "full_history.md",
            },
        )
        assert started.status_code == 200
        task_id = started.json()["task"]["task_id"]
        assert await asyncio.to_thread(author.started.wait, 30)
        cancelled = await app.request_async(
            f"/api/tasks/{task_id}/cancel", method="POST"
        )
        assert cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
        author.release.set()
        assert (await app.wait_task(task_id))["status"] == "cancelled"
        assert store.get_latest_context_checkpoint(session, vault.name) is None
        assert store.get_message_count(session, vault.name, mode="raw") >= 5
        tasks = await get_runtime_context().task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        assert (
            len(tasks) == 1
            and tasks[0].status == "cancelled"
            and tasks[0].parent_task_id == task_id
        )
