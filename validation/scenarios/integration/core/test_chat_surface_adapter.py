"""Surface-neutral chat adapter preserves authority, workspace and task events."""

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_stream_provider import streaming_provider
from validation.scenarios.support.chat_surface_journeys import (
    collect_surface_events,
)
from validation.scenarios.support.chat_task_journeys import wait_for_status
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_surface_adapter_completion_and_cancel(app: Application) -> None:
    vault = app.vault("ChatSurfaceAdapterVault")
    (vault / "Projects/Surface/Inbox").mkdir(parents=True)
    with streaming_provider() as server:
        await app.start()
        from core.chat.surface_adapter import (
            ChatSurfaceRequest,
            cancel_chat_surface_task,
            start_chat_surface_task,
        )
        from core.identity import LOCAL_USER_AUTHORITY, LOCAL_USER_PRINCIPAL_ID
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        server.release.set()
        completed = await start_chat_surface_task(
            ChatSurfaceRequest(
                surface="telegram",
                external_conversation_id="telegram-chat-123",
                vault_name=vault.name,
                session_id="telegram-session",
                prompt="surface prompt",
                model="session-ops-model",
                tools=[],
                workspace_path="Projects\\Surface/./Inbox",
                metadata={"sender": "validation-user"},
            ),
            authority=LOCAL_USER_AUTHORITY,
        )
        events = await collect_surface_events(completed.task.task_id)
        task = await app.wait_task(completed.task.task_id)
        assert {event.event for event in events} >= {"delta", "done"}
        assert task["status"] == "completed"
        snapshot = await get_runtime_context().task_coordinator.get_task(
            completed.task.task_id
        )
        assert snapshot is not None and snapshot.principal_id == LOCAL_USER_PRINCIPAL_ID
        assert (
            task["metadata"]["surface"] == "telegram"
            and task["metadata"]["external_conversation_id"] == "telegram-chat-123"
        )
        assert task["metadata"]["workspace_path"] == "Projects/Surface/Inbox"
        store = get_runtime_context().chat_store
        session = store.get_session("telegram-session", vault.name)
        assert (
            session is not None
            and session.owner_principal_id == LOCAL_USER_PRINCIPAL_ID
        )
        assert (
            store.get_session_workspace_path("telegram-session", vault.name)
            == "Projects/Surface/Inbox"
        )
        assert len(store.get_history("telegram-session", vault.name) or []) >= 2
        with pytest.raises(
            ValueError,
            match="cannot contain '..'",
        ):
            await start_chat_surface_task(
                ChatSurfaceRequest(
                    surface="telegram",
                    external_conversation_id="telegram-chat-unsafe",
                    vault_name=vault.name,
                    session_id="unsafe-workspace-session",
                    prompt="surface prompt",
                    model="session-ops-model",
                    workspace_path="../outside",
                ),
                authority=LOCAL_USER_AUTHORITY,
            )
        server.started.clear()
        server.release.clear()
        cancelled = await start_chat_surface_task(
            ChatSurfaceRequest(
                surface="discord",
                external_conversation_id="discord-channel-456",
                vault_name=vault.name,
                session_id="discord-session",
                prompt="cancel surface prompt",
                model="session-ops-model",
                tools=[],
            ),
            authority=LOCAL_USER_AUTHORITY,
        )
        assert await asyncio.to_thread(server.started.wait, 30)
        await wait_for_status(app, cancelled.task.task_id, "running")
        outcome = await cancel_chat_surface_task(cancelled.task.task_id)
        assert outcome is not None and outcome.effective
        cancel_events = await collect_surface_events(cancelled.task.task_id)
        assert any(event.event == "cancelled" for event in cancel_events)
        assert (await app.wait_task(cancelled.task.task_id))["status"] == "cancelled"
