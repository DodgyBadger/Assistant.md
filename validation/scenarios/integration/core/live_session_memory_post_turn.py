"""Validate the successful-turn boundary used by shadow session memory."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai import AgentRunResultEvent, PartStartEvent
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
            ModelRequest(parts=[UserPromptPart(content="Maintain memory.")]),
            ModelResponse(parts=[TextPart("Memory turn complete.")]),
        ]


class _CompletingAgent:
    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        yield PartStartEvent(index=0, part=TextPart("Memory turn complete."))
        yield AgentRunResultEvent(result=_FakeStreamResult())


class _RecordingSessionMemory:
    def __init__(self) -> None:
        self.recorded: list[dict] = []
        self.dispatched: list[dict] = []

    def record_completed_turn(
        self, connection, *, session_id: str, vault_name: str
    ) -> int:
        row = connection.execute(
            """
            SELECT MAX(sequence_index), COUNT(*)
            FROM chat_messages
            WHERE session_id = ? AND vault_name = ?
            """,
            (session_id, vault_name),
        ).fetchone()
        self.recorded.append(
            {
                "session_id": session_id,
                "vault_name": vault_name,
                "in_transaction": connection.in_transaction,
                "through_sequence_index": int(row[0]),
                "message_count": int(row[1]),
            }
        )
        return int(row[0])

    async def maybe_schedule_after_turn(
        self,
        *,
        session_id: str,
        vault_name: str,
        authority,
        parent_task_id: str,
    ):
        runtime = get_runtime_context()
        parent = await runtime.task_coordinator.get_task(parent_task_id)
        self.dispatched.append(
            {
                "session_id": session_id,
                "vault_name": vault_name,
                "principal_id": authority.principal_id,
                "parent_task_id": parent_task_id,
                "parent_status": parent.status if parent else None,
            }
        )
        return None


class LiveSessionMemoryPostTurnScenario(BaseScenario):
    """Prove accounting and dispatch happen at the completed-turn boundary."""

    async def test_scenario(self):
        vault = self.create_vault("LiveSessionMemoryPostTurnVault")
        await self.start_system()
        runtime = get_runtime_context()
        recording = _RecordingSessionMemory()
        runtime.session_memory = recording
        session_id = "live_session_memory_post_turn"
        ChatStore().ensure_session(
            session_id,
            vault.name,
            owner_principal_id="local-user",
        )
        event_buffer = ChatTaskEventBuffer()
        started = await start_prepared_chat_stream_task(
            prepared=PreparedChatExecution(
                agent=_CompletingAgent(),
                message_history=None,
                prompt_for_history="Maintain memory.",
                user_prompt="Maintain memory.",
                attached_image_count=0,
                model="test",
                tools=[],
            ),
            vault_name=vault.name,
            vault_path=str(vault),
            session_id=session_id,
            event_buffer=event_buffer,
        )
        completed = await self._wait_for_task_terminal(started.task.task_id)
        self.soft_assert_equal(
            completed.status if completed else None,
            "completed",
            "The chat task should complete normally",
        )
        self.soft_assert_equal(
            recording.recorded,
            [
                {
                    "session_id": session_id,
                    "vault_name": vault.name,
                    "in_transaction": True,
                    "through_sequence_index": 1,
                    "message_count": 2,
                }
            ],
            "Memory accounting should see the complete canonical turn in its transaction",
        )
        self.soft_assert_equal(
            recording.dispatched,
            [
                {
                    "session_id": session_id,
                    "vault_name": vault.name,
                    "principal_id": "local-user",
                    "parent_task_id": started.task.task_id,
                    "parent_status": "running",
                }
            ],
            "Memory dispatch should inherit authority while the chat parent is active",
        )
        events = await event_buffer.events_after(started.task.task_id)
        self.soft_assert_equal(
            [event.event for event in events],
            ["delta", "done"],
            "Memory dispatch should not alter the chat event contract",
        )
        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    async def _wait_for_task_terminal(self, task_id: str):
        runtime = get_runtime_context()
        result = await runtime.task_coordinator.wait_for_tasks(
            [task_id],
            timeout_seconds=3,
            terminal_or_attention_only=True,
        )
        return result.snapshots[0] if result.snapshots else None
