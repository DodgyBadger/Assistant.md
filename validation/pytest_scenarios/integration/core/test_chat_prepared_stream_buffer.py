"""Prepared real-agent streams honor a caller-supplied event buffer."""

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_stream_provider import streaming_provider
from validation.pytest_scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_prepared_stream_uses_isolated_event_buffer(app: Application) -> None:
    vault = app.vault("PreparedChatStreamVault")
    with streaming_provider() as server:
        await app.start()
        from core.chat.executor import PreparedChatExecution
        from core.chat.task_events import ChatTaskEventBuffer
        from core.chat.task_execution import (
            CHAT_TASK_EVENT_BUFFER,
            start_prepared_chat_stream_task,
        )
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.llm.agents import create_agent
        from core.llm.model_factory import build_model_instance
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        session = "prepared-chat-session"
        get_runtime_context().chat_store.ensure_session(
            session, vault.name, owner_principal_id="local-user"
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            model = build_model_instance("session-ops-model")
            agent = await create_agent(model=model)
        buffer = ChatTaskEventBuffer()
        server.release.set()
        started = await start_prepared_chat_stream_task(
            prepared=PreparedChatExecution(
                agent=agent,
                message_history=None,
                prompt_for_history="Run in background.",
                user_prompt="Run in background.",
                attached_image_count=0,
                model="session-ops-model",
                tools=[],
            ),
            vault_name=vault.name,
            vault_path=str(vault),
            session_id=session,
            event_buffer=buffer,
        )
        assert (await app.wait_task(started.task.task_id))["status"] == "completed"
        events = await buffer.events_after(started.task.task_id)
        assert events[-1].event == "done"
        assert any(
            event.event == "delta" and "first delta" in str(event.data)
            for event in events
        )
        assert not await CHAT_TASK_EVENT_BUFFER.has_stream(started.task.task_id)
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        messages = detail.json()["messages"]
        assert [row["role"] for row in messages] == ["user", "assistant"]
        assert "first delta final" in messages[-1]["content"]


async def test_prepared_stream_cancellation_uses_isolated_event_buffer(
    app: Application,
) -> None:
    import asyncio

    vault = app.vault("PreparedChatCancelVault")
    with streaming_provider() as server:
        await app.start()
        from core.chat.executor import PreparedChatExecution
        from core.chat.task_events import ChatTaskEventBuffer
        from core.chat.task_execution import (
            CHAT_TASK_EVENT_BUFFER,
            start_prepared_chat_stream_task,
        )
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.llm.agents import create_agent
        from core.llm.model_factory import build_model_instance
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        session = "prepared-cancel-session"
        get_runtime_context().chat_store.ensure_session(
            session, vault.name, owner_principal_id="local-user"
        )
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            agent = await create_agent(model=build_model_instance("session-ops-model"))
        buffer = ChatTaskEventBuffer()
        started = await start_prepared_chat_stream_task(
            prepared=PreparedChatExecution(
                agent=agent,
                message_history=None,
                prompt_for_history="Cancel background stream.",
                user_prompt="Cancel background stream.",
                attached_image_count=0,
                model="session-ops-model",
                tools=[],
            ),
            vault_name=vault.name,
            vault_path=str(vault),
            session_id=session,
            event_buffer=buffer,
        )
        assert await asyncio.to_thread(server.started.wait, 30)
        cancelled = await app.request_async(
            f"/api/tasks/{started.task.task_id}/cancel", method="POST"
        )
        assert cancelled.status_code == 200 and cancelled.json()["cancelled"] is True
        assert (await app.wait_task(started.task.task_id))["status"] == "cancelled"
        events = await buffer.events_after(started.task.task_id)
        assert events and events[-1].event == "cancelled"
        assert await buffer.is_terminal(started.task.task_id)
        assert not await CHAT_TASK_EVENT_BUFFER.has_stream(started.task.task_id)
