"""Validate the chat task event SSE subscription endpoint."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai import AgentRunResultEvent, PartDeltaEvent, PartStartEvent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    UserPromptPart,
)

from core.chat.chat_store import ChatStore
from core.chat.executor import PreparedChatExecution
from core.chat.task_events import ChatTaskEventBuffer
from core.chat.task_execution import (
    CHAT_TASK_EVENT_BUFFER,
    start_prepared_chat_stream_task,
    stream_chat_task_sse,
)
from core.identity import LOCAL_USER_AUTHORITY
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
)
from core.runtime.state import get_runtime_context
from core.runtime.task_runner import ExecutionTaskSpec
from validation.core.base_scenario import BaseScenario
from validation.core.streaming import stream_events_context


class _FakeStreamResult:
    def __init__(self, prompt: str, response: str) -> None:
        self._prompt = prompt
        self._response = response

    def new_messages(self):
        return [
            ModelRequest(parts=[UserPromptPart(content=self._prompt)]),
            ModelResponse(parts=[TextPart(self._response)]),
        ]


class _CompletingStreamAgent:
    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        yield PartStartEvent(index=0, part=ThinkingPart("thinking start "))
        yield PartDeltaEvent(
            index=0, delta=ThinkingPartDelta(content_delta="thinking delta")
        )
        yield PartStartEvent(index=1, part=TextPart("api "))
        yield PartDeltaEvent(index=1, delta=TextPartDelta("delta"))
        yield AgentRunResultEvent(
            result=_FakeStreamResult(
                prompt="Stream events over API.",
                response="api final response",
            )
        )


class _DeltaThenHangingStreamAgent:
    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        yield PartStartEvent(index=0, part=TextPart("still running"))
        await asyncio.Event().wait()


class ChatTaskEventStreamApiScenario(BaseScenario):
    """Validate replay and subscriber-disconnect behavior for chat task SSE."""

    async def test_scenario(self):
        vault = self.create_vault("ChatTaskEventStreamApiVault")
        await self.start_system()
        store = ChatStore()
        for session_id in (
            "chat_task_event_stream_api_session",
            "chat_task_event_pruner_session",
            "chat_task_event_disconnect_session",
        ):
            store.ensure_session(
                session_id, vault.name, owner_principal_id="local-user"
            )

        initial_detail = self.call_api(
            "/api/chat/sessions/chat_task_event_stream_api_session",
            params={"vault_name": vault.name},
        ).json()
        initial_revision = int(initial_detail.get("history_revision") or 0)
        inactive_lookup = self.call_api(
            "/api/chat/sessions/chat_task_event_stream_api_session/active-task"
        )
        self.soft_assert_equal(
            inactive_lookup.status_code,
            404,
            "A session without live work should have no active task",
        )
        self.soft_assert_equal(
            inactive_lookup.json().get("details", {}).get("history_revision"),
            initial_revision,
            "Missing active-task responses should expose a lightweight history revision",
        )

        completed = await start_prepared_chat_stream_task(
            prepared=PreparedChatExecution(
                agent=_CompletingStreamAgent(),
                message_history=None,
                prompt_for_history="Stream events over API.",
                user_prompt="Stream events over API.",
                attached_image_count=0,
                model="test",
                tools=[],
            ),
            vault_name=vault.name,
            vault_path=str(vault),
            session_id="chat_task_event_stream_api_session",
        )
        completed_task = await self._wait_for_task_terminal(completed.task.task_id)
        self.soft_assert_equal(
            completed_task.status if completed_task else None,
            "completed",
            "Started chat task should complete before event replay",
        )
        completed_lookup = self.call_api(
            "/api/chat/sessions/chat_task_event_stream_api_session/active-task"
        )
        self.soft_assert(
            completed_lookup.json().get("details", {}).get("history_revision", 0)
            > initial_revision,
            "Task completion should advance the revision returned with a missing active task",
        )

        snapshot_response = self.call_api(
            f"/api/chat/tasks/{completed.task.task_id}/replay-snapshot"
        )
        self.soft_assert_equal(
            snapshot_response.status_code,
            200,
            "Completed chat tasks should expose retained replay snapshots",
        )
        snapshot = snapshot_response.json()
        self.soft_assert_equal(
            snapshot.get("task_id"),
            completed.task.task_id,
            "Replay snapshots should identify their chat task",
        )
        self.soft_assert(
            snapshot.get("terminal") is True and snapshot.get("latest_sequence", 0) > 0,
            "Replay snapshots should expose terminal state and an atomic cursor",
        )
        snapshot_events = snapshot.get("events", [])
        self.soft_assert_equal(
            [event.get("event") for event in snapshot_events],
            ["thinking_delta", "delta", "done"],
            "Replay snapshots should compact text and preserve terminal state",
        )
        self.soft_assert(
            "thinking start thinking delta"
            in snapshot_events[0].get("delta", {}).get("content", "")
            and "api delta"
            in snapshot_events[1]
            .get("choices", [{}])[0]
            .get("delta", {})
            .get("content", ""),
            "Replay snapshots should concatenate buffered reasoning and response text",
        )

        non_chat_task = await get_runtime_context().task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.WORKFLOW,
                scope="workflow:replay-snapshot-probe",
                source=ExecutionTaskSource.SYSTEM,
                label="replay-snapshot-probe",
                authority=LOCAL_USER_AUTHORITY,
            ),
            _complete_task,
        )
        await self._wait_for_task_terminal(non_chat_task.task_id)
        non_chat_snapshot = self.call_api(
            f"/api/chat/tasks/{non_chat_task.task_id}/replay-snapshot"
        )
        self.soft_assert_equal(
            non_chat_snapshot.status_code,
            404,
            "Replay snapshots should reject non-chat execution tasks",
        )

        queued_chat_task = await get_runtime_context().task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="chat:queued-replay-snapshot-probe",
                source=ExecutionTaskSource.SYSTEM,
                label="queued-replay-snapshot-probe",
                authority=LOCAL_USER_AUTHORITY,
            ),
            _complete_task,
            start_immediately=False,
        )
        queued_snapshot_response = self.call_api(
            f"/api/chat/tasks/{queued_chat_task.task_id}/replay-snapshot"
        )
        self.soft_assert_equal(
            queued_snapshot_response.status_code,
            200,
            "Queued chat tasks should expose an empty replay snapshot",
        )
        queued_snapshot = queued_snapshot_response.json()
        self.soft_assert_equal(
            (queued_snapshot.get("latest_sequence"), queued_snapshot.get("events")),
            (0, []),
            "A chat task without events should begin at cursor zero",
        )
        await get_runtime_context().task_coordinator.cancel_task(
            queued_chat_task.task_id,
            reason="validation_cleanup",
        )

        replay = self.call_api(f"/api/chat/tasks/{completed.task.task_id}/events")
        self.soft_assert_equal(
            replay.status_code,
            200,
            "Chat task event stream endpoint should return SSE for a chat task",
        )
        self.soft_assert(
            '"event": "thinking_delta"' in replay.text
            and "thinking start " in replay.text
            and "thinking delta" in replay.text
            and '"event": "delta"' in replay.text
            and '"event": "done"' in replay.text,
            "Chat task event stream should replay buffered thinking, delta, and done events",
        )
        self.soft_assert(
            "api " in replay.text and "delta" in replay.text,
            "Replayed SSE stream should include the buffered delta text",
        )

        replay_after_delta = self.call_api(
            f"/api/chat/tasks/{completed.task.task_id}/events",
            params={"after_sequence": 4},
        )
        self.soft_assert_equal(
            replay_after_delta.status_code,
            200,
            "Chat task event stream should support cursor replay",
        )
        self.soft_assert(
            '"event": "thinking_delta"' not in replay_after_delta.text
            and '"event": "delta"' not in replay_after_delta.text
            and '"event": "done"' in replay_after_delta.text,
            "Cursor replay should skip events at or before after_sequence",
        )

        original_event_limit = (
            CHAT_TASK_EVENT_BUFFER._max_events_per_task
        )  # noqa: SLF001
        CHAT_TASK_EVENT_BUFFER._max_events_per_task = 2  # noqa: SLF001
        try:
            overflowed = await start_prepared_chat_stream_task(
                prepared=PreparedChatExecution(
                    agent=_CompletingStreamAgent(),
                    message_history=None,
                    prompt_for_history="Expire the initial event cursor.",
                    user_prompt="Expire the initial event cursor.",
                    attached_image_count=0,
                    model="test",
                    tools=[],
                ),
                vault_name=vault.name,
                vault_path=str(vault),
                session_id="chat_task_event_stream_api_session",
            )
            overflowed_task = await self._wait_for_task_terminal(
                overflowed.task.task_id
            )
            self.soft_assert_equal(
                overflowed_task.status if overflowed_task else None,
                "completed",
                "Overflow probe should complete before cursor expiry is queried",
            )
            expired_cursor = self.call_api(
                f"/api/chat/tasks/{overflowed.task.task_id}/events",
                params={"after_sequence": 0},
            )
            self.soft_assert_equal(
                expired_cursor.status_code,
                410,
                "A cursor before the retained event window should be rejected",
            )
            self.soft_assert(
                "ChatTaskEventCursorExpired" in expired_cursor.text
                and "oldest_available_sequence" in expired_cursor.text,
                "Cursor expiry should return a stable recovery envelope",
            )
            overflow_snapshot_response = self.call_api(
                f"/api/chat/tasks/{overflowed.task.task_id}/replay-snapshot"
            )
            self.soft_assert_equal(
                overflow_snapshot_response.status_code,
                200,
                "A compact replay snapshot should survive raw cursor expiry",
            )
            overflow_snapshot = overflow_snapshot_response.json()
            self.soft_assert(
                any(
                    event.get("event") == "delta"
                    and "api delta"
                    in event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    for event in overflow_snapshot.get("events", [])
                ),
                "Expired raw replay should retain the complete projected response",
            )
            handoff = self.call_api(
                f"/api/chat/tasks/{overflowed.task.task_id}/events",
                params={"after_sequence": overflow_snapshot["latest_sequence"]},
            )
            self.soft_assert_equal(
                handoff.status_code,
                200,
                "The snapshot cursor should be accepted by the SSE endpoint",
            )
            self.soft_assert_equal(
                handoff.text,
                "",
                "A terminal snapshot cursor should not replay duplicate events",
            )
            race_buffer = ChatTaskEventBuffer(max_events_per_task=2)
            await race_buffer.append("cursor-race", "delta", {"index": 1})
            await race_buffer.append("cursor-race", "delta", {"index": 2})
            await race_buffer.append("cursor-race", "delta", {"index": 3})
            race_stream = stream_chat_task_sse(
                task_id="cursor-race",
                event_buffer=race_buffer,
                after_sequence=0,
            )
            race_payload = await race_stream.__anext__()
            self.soft_assert(
                "chat_event_cursor_expired" in race_payload,
                "A cursor gap discovered after API preflight should close the SSE stream explicitly",
            )
        finally:
            CHAT_TASK_EVENT_BUFFER._max_events_per_task = (  # noqa: SLF001
                original_event_limit
            )

        original_terminal_limit = (
            CHAT_TASK_EVENT_BUFFER._max_terminal_tasks
        )  # noqa: SLF001
        CHAT_TASK_EVENT_BUFFER._max_terminal_tasks = 1  # noqa: SLF001
        try:
            pruner = await start_prepared_chat_stream_task(
                prepared=PreparedChatExecution(
                    agent=_CompletingStreamAgent(),
                    message_history=None,
                    prompt_for_history="Prune older stream events.",
                    user_prompt="Prune older stream events.",
                    attached_image_count=0,
                    model="test",
                    tools=[],
                ),
                vault_name=vault.name,
                vault_path=str(vault),
                session_id="chat_task_event_pruner_session",
            )
            pruner_task = await self._wait_for_task_terminal(pruner.task.task_id)
            self.soft_assert_equal(
                pruner_task.status if pruner_task else None,
                "completed",
                "Pruner chat task should complete before expired replay check",
            )

            expired_replay = self.call_api(
                f"/api/chat/tasks/{completed.task.task_id}/events"
            )
            self.soft_assert_equal(
                expired_replay.status_code,
                410,
                "Expired terminal chat task event streams should return a terminal API response",
            )
            self.soft_assert(
                "ChatTaskEventsExpired" in expired_replay.text,
                "Expired terminal chat task event response should identify the retention miss",
            )
        finally:
            CHAT_TASK_EVENT_BUFFER._max_terminal_tasks = (
                original_terminal_limit  # noqa: SLF001
            )

        running = await start_prepared_chat_stream_task(
            prepared=PreparedChatExecution(
                agent=_DeltaThenHangingStreamAgent(),
                message_history=None,
                prompt_for_history="Keep running after SSE disconnect.",
                user_prompt="Keep running after SSE disconnect.",
                attached_image_count=0,
                model="test",
                tools=[],
            ),
            vault_name=vault.name,
            vault_path=str(vault),
            session_id="chat_task_event_disconnect_session",
        )
        running_task = await self._wait_for_task_running(running.task.task_id)
        self.soft_assert_equal(
            running_task.status if running_task else None,
            "running",
            "Second chat task should be running before SSE subscriber disconnect",
        )

        sse_stream = stream_chat_task_sse(
            task_id=running.task.task_id,
            keepalive_seconds=0.05,
        )
        try:
            first_payload = await sse_stream.__anext__()
            self.soft_assert(
                "still running" in first_payload,
                "Running event stream should deliver the initial delta",
            )
        finally:
            await sse_stream.aclose()

        after_disconnect = await get_runtime_context().task_coordinator.get_task(
            running.task.task_id
        )
        self.soft_assert_equal(
            after_disconnect.status if after_disconnect else None,
            "running",
            "Closing the SSE subscriber should not cancel the chat task",
        )
        detached_snapshot = self.call_api(
            f"/api/chat/tasks/{running.task.task_id}/replay-snapshot"
        )
        self.soft_assert_equal(
            detached_snapshot.status_code,
            200,
            "Running chat snapshots should remain available without an SSE subscriber",
        )
        self.soft_assert(
            any(
                event.get("event") == "delta"
                and "still running"
                in event.get("choices", [{}])[0].get("delta", {}).get("content", "")
                for event in detached_snapshot.json().get("events", [])
            ),
            "Detached snapshots should retain the in-progress response",
        )
        await get_runtime_context().task_coordinator.cancel_task(running.task.task_id)
        cancelled_task = await self._wait_for_task_terminal(running.task.task_id)
        self.soft_assert_equal(
            cancelled_task.status if cancelled_task else None,
            "cancelled",
            "Explicit task cancellation should still cancel the running chat task",
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


async def _complete_task(_task: ExecutionTaskSnapshot) -> None:
    await asyncio.sleep(0)
