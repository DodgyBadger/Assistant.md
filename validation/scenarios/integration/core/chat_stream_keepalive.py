"""Validate streaming chat emits keepalives while waiting for agent events."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.chat_store import ChatStore
from core.chat.executor import PreparedChatExecution
from core.chat.task_execution import (
    start_prepared_chat_stream_task,
    stream_chat_task_sse,
)
from core.runtime.state import get_runtime_context
from validation.support.base_scenario import BaseScenario
from validation.support.streaming import stream_events_context


class _DelayedStreamAgent:
    """Fake agent that stays idle long enough to require a keepalive."""

    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        await asyncio.sleep(0.05)
        if False:
            yield None


class _HangingStreamAgent:
    """Fake streaming agent that stays active until cancelled."""

    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        await asyncio.Event().wait()
        if False:
            yield None


class ChatStreamKeepaliveScenario(BaseScenario):
    """Validate SSE keepalives are emitted during idle streaming waits."""

    async def test_scenario(self):
        vault = self.create_vault("ChatStreamKeepaliveVault")
        await self.start_system()
        store = ChatStore()
        for session_id in (
            "chat_stream_keepalive_session",
            "chat_stream_keepalive_cancel_session",
        ):
            store.ensure_session(
                session_id, vault.name, owner_principal_id="local-user"
            )

        try:
            idle = await start_prepared_chat_stream_task(
                prepared=PreparedChatExecution(
                    agent=_DelayedStreamAgent(),
                    message_history=None,
                    prompt_for_history="Trigger idle streaming.",
                    user_prompt="Trigger idle streaming.",
                    attached_image_count=0,
                    model="test",
                    tools=[],
                ),
                vault_name=vault.name,
                vault_path=str(vault),
                session_id="chat_stream_keepalive_session",
            )
            chunks: list[str] = []
            async for chunk in stream_chat_task_sse(
                task_id=idle.task.task_id,
                keepalive_seconds=0.01,
            ):
                chunks.append(chunk)

            keepalive_index = next(
                (
                    index
                    for index, chunk in enumerate(chunks)
                    if chunk.startswith(": keepalive")
                ),
                None,
            )
            done_index = next(
                (
                    index
                    for index, chunk in enumerate(chunks)
                    if '"event": "done"' in chunk
                ),
                None,
            )
            self.soft_assert(
                keepalive_index is not None, "Idle stream should emit a keepalive chunk"
            )
            self.soft_assert(
                done_index is not None, "Idle stream should still emit the done event"
            )
            if keepalive_index is not None and done_index is not None:
                self.soft_assert(
                    keepalive_index < done_index,
                    "Keepalive should arrive before the terminal done event",
                )

            hanging = await start_prepared_chat_stream_task(
                prepared=PreparedChatExecution(
                    agent=_HangingStreamAgent(),
                    message_history=None,
                    prompt_for_history="Cancel idle streaming.",
                    user_prompt="Cancel idle streaming.",
                    attached_image_count=0,
                    model="test",
                    tools=[],
                ),
                vault_name=vault.name,
                vault_path=str(vault),
                session_id="chat_stream_keepalive_cancel_session",
            )
            cancel_chunks: list[str] = []

            async def _consume_stream() -> None:
                async for chunk in stream_chat_task_sse(
                    task_id=hanging.task.task_id,
                    keepalive_seconds=0.01,
                ):
                    cancel_chunks.append(chunk)

            consume_task = asyncio.create_task(_consume_stream())
            try:
                for _ in range(50):
                    task_snapshot = (
                        await get_runtime_context().task_coordinator.get_task(
                            hanging.task.task_id
                        )
                    )
                    if task_snapshot is not None and task_snapshot.status == "running":
                        break
                    await asyncio.sleep(0.02)

                self.soft_assert(
                    task_snapshot is not None and task_snapshot.status == "running",
                    "Streaming run should expose a running chat task",
                )
                cancellation = await get_runtime_context().task_coordinator.cancel_task(
                    hanging.task.task_id
                )
                self.soft_assert(
                    cancellation is not None and cancellation.effective is True,
                    "Streaming chat session cancel should be effective",
                )
                await asyncio.wait_for(consume_task, timeout=1)
                self.soft_assert(
                    any('"event": "cancelled"' in chunk for chunk in cancel_chunks),
                    "Streaming cancellation should emit the cancelled SSE event",
                )
            finally:
                if not consume_task.done():
                    consume_task.cancel()
        finally:
            await self.stop_system()
            self.teardown_scenario()

        self.assert_no_failures()
