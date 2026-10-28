"""Validate per-session queuing for task-owned chat execution."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai import AgentRunResultEvent, PartStartEvent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from api.services.execution_tasks import get_active_chat_task
from core.chat import executor as chat_executor
from core.chat.chat_store import ChatStore
from core.chat.executor import PreparedChatExecution
from core.chat.task_execution import start_queued_chat_stream_task, stream_chat_task_sse
from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
from core.runtime.state import get_runtime_context
from validation.support.base_scenario import BaseScenario
from validation.support.streaming import stream_events_context


class _FakeStreamResult:
    def __init__(self, prompt: str, response: str) -> None:
        self._prompt = prompt
        self._response = response

    def new_messages(self):
        return [
            ModelRequest(parts=[UserPromptPart(content=self._prompt)]),
            ModelResponse(parts=[TextPart(self._response)]),
        ]


class _BlockingStreamAgent:
    def __init__(
        self, release: asyncio.Event, *, delta: str, prompt: str, response: str
    ) -> None:
        self._release = release
        self._delta = delta
        self._prompt = prompt
        self._response = response

    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        yield PartStartEvent(index=0, part=TextPart(self._delta))
        await self._release.wait()
        yield AgentRunResultEvent(
            result=_FakeStreamResult(
                prompt=self._prompt,
                response=self._response,
            )
        )


class _CompletingStreamAgent:
    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        yield PartStartEvent(index=0, part=TextPart("second delta"))
        yield AgentRunResultEvent(
            result=_FakeStreamResult(
                prompt="second prompt",
                response="second final response",
            )
        )


class ChatTaskSessionQueueScenario(BaseScenario):
    """Validate that task-owned chat runs serialize per session."""

    async def test_scenario(self):
        vault = self.create_vault("ChatTaskSessionQueueVault")
        await self.start_system()
        store = ChatStore()
        for session_id in (
            "queued-chat-session",
            "cancel-queued-chat-session",
            "cancel-preflight-chat-session",
        ):
            store.ensure_session(
                session_id, vault.name, owner_principal_id="local-user"
            )

        release_first = asyncio.Event()
        release_cancel_blocker = asyncio.Event()
        slow_preflight_started = asyncio.Event()
        prepare_history_counts: dict[str, int] = {}
        prepared_modes: dict[str, str] = {}
        original_prepare = chat_executor._prepare_chat_execution

        async def fake_prepare_chat_execution(
            *,
            vault_name,
            vault_path,
            prompt,
            image_paths,
            image_uploads,
            session_id,
            tools,
            model,
            thinking=None,
            context_template=None,
            chat_mode="normal",
            display_prompt=None,
        ):
            prepared_modes[prompt] = chat_mode
            del (
                vault_path,
                image_paths,
                image_uploads,
                thinking,
                chat_mode,
                display_prompt,
            )
            if prompt == "slow preflight prompt":
                slow_preflight_started.set()
                await asyncio.Event().wait()
            history = (
                chat_executor._CHAT_STORE.get_history(session_id, vault_name) or []
            )
            prepare_history_counts[prompt] = len(history)
            if prompt == "first prompt":
                agent = _BlockingStreamAgent(
                    release_first,
                    delta="first delta",
                    prompt="first prompt",
                    response="first final response",
                )
            elif prompt == "cancel blocker prompt":
                agent = _BlockingStreamAgent(
                    release_cancel_blocker,
                    delta="cancel blocker delta",
                    prompt="cancel blocker prompt",
                    response="cancel blocker final response",
                )
            else:
                agent = _CompletingStreamAgent()
            return PreparedChatExecution(
                agent=agent,
                message_history=list(history),
                prompt_for_history=prompt,
                user_prompt=prompt,
                attached_image_count=0,
                model=model,
                tools=list(tools),
                context_template=context_template,
            )

        chat_executor._prepare_chat_execution = fake_prepare_chat_execution
        try:
            first = await start_queued_chat_stream_task(
                vault_name=vault.name,
                vault_path=str(vault),
                prompt="first prompt",
                image_paths=[],
                image_uploads=[],
                session_id="queued-chat-session",
                tools=[],
                model="test",
            )
            first_running = await self._wait_for_task_status(
                first.task.task_id, "running"
            )
            self.soft_assert_equal(
                first_running.status if first_running else None,
                "running",
                "First queued chat task should start running",
            )

            second = await start_queued_chat_stream_task(
                vault_name=vault.name,
                vault_path=str(vault),
                prompt="second prompt",
                image_paths=[],
                image_uploads=[],
                session_id="queued-chat-session",
                tools=[],
                model="test",
            )
            second_queued = await self._wait_for_task_status(
                second.task.task_id, "queued"
            )
            self.soft_assert_equal(
                second_queued.status if second_queued else None,
                "queued",
                "Second chat task should remain queued while first is running",
            )
            self.soft_assert(
                "second prompt" not in prepare_history_counts,
                "Queued chat task should not prepare before earlier task completes",
            )
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                active = await get_active_chat_task("queued-chat-session")
            self.soft_assert_equal(
                active.task_id,
                first.task.task_id,
                "Session active-task lookup should prefer the running task over queued work",
            )

            # A preference change after acceptance must survive queued execution,
            # while both accepted turns retain their original approval policy.
            store.set_session_chat_mode(
                session_id="queued-chat-session",
                vault_name=vault.name,
                chat_mode="inline_edit",
            )
            release_first.set()
            first_terminal = await self._wait_for_task_terminal(first.task.task_id)
            second_terminal = await self._wait_for_task_terminal(second.task.task_id)

            self.soft_assert_equal(
                store.get_session_chat_mode("queued-chat-session", vault.name),
                "inline_edit",
                "Queued execution must preserve a newer session approval preference",
            )
            self.soft_assert_equal(
                prepared_modes.get("second prompt"),
                "normal",
                "Queued execution must retain its accepted approval mode",
            )

            cancel_blocker = await start_queued_chat_stream_task(
                vault_name=vault.name,
                vault_path=str(vault),
                prompt="cancel blocker prompt",
                image_paths=[],
                image_uploads=[],
                session_id="cancel-queued-chat-session",
                tools=[],
                model="test",
            )
            cancel_blocker_running = await self._wait_for_task_status(
                cancel_blocker.task.task_id,
                "running",
            )
            self.soft_assert_equal(
                cancel_blocker_running.status if cancel_blocker_running else None,
                "running",
                "Queue cancellation blocker should start running",
            )
            queued_cancel = await start_queued_chat_stream_task(
                vault_name=vault.name,
                vault_path=str(vault),
                prompt="queued cancel prompt",
                image_paths=[],
                image_uploads=[],
                session_id="cancel-queued-chat-session",
                tools=[],
                model="test",
            )
            queued_cancel_snapshot = await self._wait_for_task_status(
                queued_cancel.task.task_id,
                "queued",
            )
            self.soft_assert_equal(
                queued_cancel_snapshot.status if queued_cancel_snapshot else None,
                "queued",
                "Queued cancellation target should wait behind the blocker",
            )
            await get_runtime_context().task_coordinator.cancel_task(
                queued_cancel.task.task_id,
                reason="validation_cancel_queued",
            )
            queued_cancel_events = await self._collect_stream_until_terminal(
                queued_cancel.task.task_id,
            )
            queued_cancel_terminal = await self._wait_for_task_terminal(
                queued_cancel.task.task_id,
            )
            release_cancel_blocker.set()
            await self._wait_for_task_terminal(cancel_blocker.task.task_id)

            slow_preflight = await start_queued_chat_stream_task(
                vault_name=vault.name,
                vault_path=str(vault),
                prompt="slow preflight prompt",
                image_paths=[],
                image_uploads=[],
                session_id="cancel-preflight-chat-session",
                tools=[],
                model="test",
            )
            await asyncio.wait_for(slow_preflight_started.wait(), timeout=2.0)
            await get_runtime_context().task_coordinator.cancel_task(
                slow_preflight.task.task_id,
                reason="validation_cancel_preflight",
            )
            slow_preflight_events = await self._collect_stream_until_terminal(
                slow_preflight.task.task_id,
            )
            slow_preflight_terminal = await self._wait_for_task_terminal(
                slow_preflight.task.task_id,
            )
        finally:
            chat_executor._prepare_chat_execution = original_prepare

        self.soft_assert_equal(
            first_terminal.status if first_terminal else None,
            "completed",
            "First queued chat task should complete",
        )
        self.soft_assert_equal(
            second_terminal.status if second_terminal else None,
            "completed",
            "Second queued chat task should complete after first",
        )
        self.soft_assert(
            prepare_history_counts.get("second prompt", 0) >= 2,
            "Second queued chat task should prepare after first history is persisted",
        )

        second_events = ""
        async for chunk in stream_chat_task_sse(task_id=second.task.task_id):
            second_events += chunk
        self.soft_assert(
            "second delta" in second_events and '"event": "done"' in second_events,
            "Second queued task should publish replayable stream events",
        )
        self.soft_assert_equal(
            queued_cancel_terminal.status if queued_cancel_terminal else None,
            "cancelled",
            "Cancelled queued chat task should become terminal",
        )
        self.soft_assert(
            '"event": "cancelled"' in queued_cancel_events,
            "Cancelled queued chat task should publish a terminal stream event",
        )
        self.soft_assert(
            "queued cancel prompt" not in prepare_history_counts,
            "Cancelled queued chat task should not run preflight",
        )
        self.soft_assert_equal(
            slow_preflight_terminal.status if slow_preflight_terminal else None,
            "cancelled",
            "Chat task cancelled during preflight should become terminal",
        )
        self.soft_assert(
            '"event": "cancelled"' in slow_preflight_events,
            "Chat task cancelled during preflight should publish a terminal stream event",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    async def _wait_for_task_status(self, task_id: str, status: str):
        runtime = get_runtime_context()
        for _ in range(100):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.status == status:
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

    async def _collect_stream_until_terminal(self, task_id: str) -> str:
        async def _collect() -> str:
            chunks = []
            async for chunk in stream_chat_task_sse(
                task_id=task_id, keepalive_seconds=0.05
            ):
                chunks.append(chunk)
            return "".join(chunks)

        return await asyncio.wait_for(_collect(), timeout=2.0)
