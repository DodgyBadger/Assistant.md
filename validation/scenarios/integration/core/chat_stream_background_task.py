"""Validate task-owned streaming chat execution without API wiring."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai import AgentRunResultEvent, PartStartEvent, TextPartDelta
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from core.chat.chat_store import ChatStore
from core.chat.executor import PreparedChatExecution
from core.chat.task_events import ChatTaskEventBuffer
from core.chat.task_execution import start_prepared_chat_stream_task
from core.runtime.state import get_runtime_context
from validation.core.base_scenario import BaseScenario
from validation.core.streaming import stream_events_context


class _FakeStreamResult:
    def new_messages(self):
        return [
            ModelRequest(parts=[UserPromptPart(content="Run in background.")]),
            ModelResponse(parts=[TextPart("background response")]),
        ]


class _CompletingStreamAgent:
    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        yield PartStartEvent(index=0, part=TextPart("background "))
        yield AgentRunResultEvent(result=_FakeStreamResult())


class _HangingStreamAgent:
    def __init__(self) -> None:
        self.stream_entered = asyncio.Event()

    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        self.stream_entered.set()
        await asyncio.Event().wait()
        if False:
            yield TextPartDelta("unreachable")


class ChatStreamBackgroundTaskScenario(BaseScenario):
    """Validate background chat stream task completion and cancellation."""

    async def test_scenario(self):
        vault = self.create_vault("ChatStreamBackgroundTaskVault")
        await self.start_system()
        store = ChatStore()
        for session_id in (
            "chat_stream_background_task_session",
            "chat_stream_background_cancel_session",
        ):
            store.ensure_session(
                session_id, vault.name, owner_principal_id="local-user"
            )

        runtime = get_runtime_context()
        event_buffer = ChatTaskEventBuffer()

        start = await start_prepared_chat_stream_task(
            prepared=PreparedChatExecution(
                agent=_CompletingStreamAgent(),
                message_history=None,
                prompt_for_history="Run in background.",
                user_prompt="Run in background.",
                attached_image_count=0,
                model="test",
                tools=[],
            ),
            vault_name=vault.name,
            vault_path=str(vault),
            session_id="chat_stream_background_task_session",
            event_buffer=event_buffer,
        )

        completed_task = await self._wait_for_task_terminal(start.task.task_id)
        self.soft_assert_equal(
            completed_task.status if completed_task else None,
            "completed",
            "Background streaming chat task should complete",
        )
        events = await event_buffer.events_after(start.task.task_id)
        self.soft_assert_equal(
            [event.event for event in events],
            ["delta", "done"],
            "Background worker should publish delta and terminal done events",
        )
        self.soft_assert_equal(
            events[0].data["choices"][0]["delta"]["content"],
            "background ",
            "Buffered delta payload should preserve streamed text",
        )

        detail = self.call_api(
            f"/api/chat/sessions/{start.session_id}?vault_name={vault.name}"
        )
        self.soft_assert_equal(
            detail.status_code,
            200,
            "Background streaming chat session detail should be available",
        )
        messages = detail.json().get("messages", [])
        self.soft_assert_equal(
            [message.get("role") for message in messages],
            ["user", "assistant"],
            "Completed background stream should persist user and assistant messages",
        )
        self.soft_assert(
            "background response" in messages[-1].get("content", ""),
            "Persisted assistant message should come from final run result",
        )

        hanging_agent = _HangingStreamAgent()
        cancel_start = await start_prepared_chat_stream_task(
            prepared=PreparedChatExecution(
                agent=hanging_agent,
                message_history=None,
                prompt_for_history="Cancel background stream.",
                user_prompt="Cancel background stream.",
                attached_image_count=0,
                model="test",
                tools=[],
            ),
            vault_name=vault.name,
            vault_path=str(vault),
            session_id="chat_stream_background_cancel_session",
            event_buffer=event_buffer,
        )
        running_task = await self._wait_for_task_running(cancel_start.task.task_id)
        self.soft_assert_equal(
            running_task.status if running_task else None,
            "running",
            "Background streaming chat task should enter running state",
        )
        await asyncio.wait_for(hanging_agent.stream_entered.wait(), timeout=2.0)
        cancellation = await runtime.task_coordinator.cancel_task(
            cancel_start.task.task_id
        )
        self.soft_assert(
            cancellation is not None and cancellation.effective,
            "Task cancellation should be effective for background streaming chat",
        )
        cancelled_task = await self._wait_for_task_terminal(cancel_start.task.task_id)
        self.soft_assert_equal(
            cancelled_task.status if cancelled_task else None,
            "cancelled",
            "Cancelled background streaming chat task should become terminal",
        )
        cancel_events = await event_buffer.events_after(cancel_start.task.task_id)
        self.soft_assert(
            any(event.event == "cancelled" for event in cancel_events),
            "Cancelled background worker should publish a cancelled event",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    async def _wait_for_task_running(self, task_id: str):
        runtime = get_runtime_context()
        for _ in range(50):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.status == "running":
                return task
            await asyncio.sleep(0.02)
        return None

    async def _wait_for_task_terminal(self, task_id: str):
        runtime = get_runtime_context()
        for _ in range(100):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.is_terminal:
                return task
            await asyncio.sleep(0.02)
        return None
