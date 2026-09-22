"""Validate process-local chat task event buffer behavior."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.task_events import ChatTaskEventBuffer, ChatTaskEventCursorExpired
from validation.core.base_scenario import BaseScenario


class ChatTaskEventBufferScenario(BaseScenario):
    """Validate replay, wakeup, terminal close, and retention semantics."""

    async def test_scenario(self):
        buffer = ChatTaskEventBuffer(max_events_per_task=3, max_terminal_tasks=1)

        first = await buffer.append(
            "task-alpha",
            "delta",
            {"text": "hello"},
        )
        second = await buffer.append(
            "task-alpha",
            "tool_call_started",
            {"tool_name": "delegate"},
        )
        replay = await buffer.events_after("task-alpha", after_sequence=0)
        self.soft_assert_equal(
            [event.sequence for event in replay],
            [first.sequence, second.sequence],
            "Buffered events should replay in sequence order",
        )
        self.soft_assert_equal(
            replay[0].data,
            {"text": "hello"},
            "Buffered event data should be preserved",
        )

        subscriber_events = []

        async def _collect_until_done() -> None:
            async for event in buffer.subscribe("task-beta"):
                subscriber_events.append(event)

        subscriber = asyncio.create_task(_collect_until_done())
        try:
            await asyncio.sleep(0)
            await buffer.append("task-beta", "delta", {"text": "wake"})
            await buffer.append("task-beta", "done", {"finish_reason": "stop"})
            await asyncio.wait_for(subscriber, timeout=1)
        finally:
            if not subscriber.done():
                subscriber.cancel()

        self.soft_assert_equal(
            [event.event for event in subscriber_events],
            ["delta", "done"],
            "Subscriber should wake for new events and stop at terminal event",
        )
        self.soft_assert(
            await buffer.is_terminal("task-beta"),
            "Terminal event should mark the task event stream terminal",
        )

        terminal_replay = []
        async for event in buffer.subscribe("task-beta", after_sequence=2):
            terminal_replay.append(event)
        self.soft_assert_equal(
            terminal_replay,
            [],
            "Subscribing after the terminal sequence should close immediately",
        )

        retained = ChatTaskEventBuffer(max_events_per_task=2, max_terminal_tasks=1)
        await retained.append("task-gamma", "delta", {"index": 1})
        await retained.append("task-gamma", "delta", {"index": 2})
        await retained.append("task-gamma", "done", {"index": 3})
        gamma_events = await retained.events_after("task-gamma", after_sequence=1)
        self.soft_assert_equal(
            [event.sequence for event in gamma_events],
            [2, 3],
            "Per-task event retention should keep the newest events",
        )
        try:
            await retained.events_after("task-gamma", after_sequence=0)
        except ChatTaskEventCursorExpired as exc:
            self.soft_assert_equal(
                exc.oldest_available_sequence,
                2,
                "Expired cursors should identify the oldest retained event",
            )
            self.soft_assert_equal(
                exc.latest_sequence,
                3,
                "Expired cursors should identify the latest retained event",
            )
        else:
            self.soft_assert(False, "A cursor before the retained window should fail")

        projection = ChatTaskEventBuffer(max_events_per_task=2, max_terminal_tasks=2)
        await projection.append(
            "task-projection",
            "thinking_delta",
            {"event": "thinking_delta", "delta": {"content": "think "}},
        )
        await projection.append(
            "task-projection",
            "thinking_delta",
            {"event": "thinking_delta", "delta": {"content": "more"}},
        )
        await projection.append(
            "task-projection",
            "delta",
            {
                "event": "delta",
                "choices": [{"delta": {"content": "hello "}, "index": 0}],
            },
        )
        await projection.append(
            "task-projection",
            "delta",
            {
                "event": "delta",
                "choices": [{"delta": {"content": "world"}, "index": 0}],
            },
        )
        await projection.append(
            "task-projection",
            "tool_call_started",
            {
                "event": "tool_call_started",
                "tool_call_id": "tool-1",
                "tool_name": "read_file",
            },
        )
        before_finish = await projection.replay_snapshot("task-projection")
        self.soft_assert(
            before_finish is not None, "Active streams should have snapshots"
        )
        if before_finish is not None:
            self.soft_assert_equal(
                before_finish.latest_sequence,
                5,
                "Snapshot cursors should include every reduced raw event",
            )
            self.soft_assert_equal(
                [event.event for event in before_finish.events],
                ["thinking_delta", "delta", "tool_call_started"],
                "Snapshots should collapse text while preserving current tool state",
            )
            self.soft_assert_equal(
                before_finish.events[0].data["delta"]["content"],
                "think more",
                "Reasoning deltas should collapse into one snapshot event",
            )
            self.soft_assert_equal(
                before_finish.events[1].data["choices"][0]["delta"]["content"],
                "hello world",
                "Response deltas should collapse into one snapshot event",
            )

        await projection.append(
            "task-projection",
            "tool_call_started",
            {
                "event": "tool_call_started",
                "tool_call_id": "tool-2",
                "tool_name": "search",
            },
        )
        await projection.append(
            "task-projection",
            "tool_call_finished",
            {
                "event": "tool_call_finished",
                "tool_call_id": "tool-1",
                "tool_name": "read_file",
                "terminal_state": "completed",
                "token_count": 7,
            },
        )
        await projection.append(
            "task-projection",
            "chat_retry_scheduled",
            {"event": "chat_retry_scheduled", "reset_response": True},
        )
        await projection.append(
            "task-projection",
            "delta",
            {
                "event": "delta",
                "choices": [{"delta": {"content": "replacement"}, "index": 0}],
            },
        )
        await projection.append(
            "task-projection",
            "review_required",
            {"event": "review_required", "artifact_ref": "review-1"},
        )
        snapshot = await projection.replay_snapshot("task-projection")
        self.soft_assert(
            snapshot is not None, "Retained streams should remain snapshotable"
        )
        if snapshot is not None:
            self.soft_assert_equal(
                snapshot.latest_sequence,
                10,
                "Snapshot cursor should advance across omitted control events",
            )
            self.soft_assert_equal(
                [event.event for event in snapshot.events],
                [
                    "tool_call_started",
                    "tool_call_started",
                    "tool_call_finished",
                    "delta",
                    "review_required",
                ],
                "Retry reset should remove stale text and preserve effective UI state",
            )
            self.soft_assert_equal(
                [
                    event.data.get("tool_call_id")
                    for event in snapshot.events
                    if event.event == "tool_call_started"
                ],
                ["tool-1", "tool-2"],
                "Tool updates should preserve original display order",
            )
            self.soft_assert_equal(
                snapshot.events[-2].event,
                "delta",
                "Projected events should retain effective sequence order for current status",
            )
            self.soft_assert_equal(
                next(event for event in snapshot.events if event.event == "delta").data[
                    "choices"
                ][0]["delta"]["content"],
                "replacement",
                "Snapshot response should contain only post-retry content",
            )
            self.soft_assert(
                all(
                    "arguments" not in event.data and "result" not in event.data
                    for event in snapshot.events
                ),
                "Snapshot tool state should not expose tool arguments or results",
            )

            await projection.append(
                "task-projection",
                "done",
                {
                    "event": "done",
                    "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
                },
            )
            later = await projection.events_after(
                "task-projection", after_sequence=snapshot.latest_sequence
            )
            self.soft_assert_equal(
                [event.event for event in later],
                ["done"],
                "Events after an atomic snapshot cursor should contain only newer events",
            )
            terminal_snapshot = await projection.replay_snapshot("task-projection")
            self.soft_assert_equal(
                terminal_snapshot.events[-1].event if terminal_snapshot else None,
                "done",
                "Terminal state should remain reconstructable in a snapshot",
            )

        redirect_buffer = ChatTaskEventBuffer(max_events_per_task=1)
        await redirect_buffer.append(
            "task-redirect",
            "delta",
            {
                "event": "delta",
                "choices": [{"delta": {"content": "discard me"}, "index": 0}],
            },
        )
        await redirect_buffer.append(
            "task-redirect",
            "chat_retry_redirect",
            {
                "event": "chat_retry_redirect",
                "replacement_task_id": "replacement-task",
                "reset_response": True,
            },
        )
        redirect_snapshot = await redirect_buffer.replay_snapshot("task-redirect")
        self.soft_assert_equal(
            (
                [event.event for event in redirect_snapshot.events]
                if redirect_snapshot
                else []
            ),
            ["chat_retry_redirect"],
            "Terminal retry redirects should survive raw event trimming without stale text",
        )
        self.soft_assert_equal(
            await projection.replay_snapshot("missing-task"),
            None,
            "Unknown streams should not synthesize replay state",
        )

        future_events = ChatTaskEventBuffer(max_events_per_task=3)
        await future_events.append(
            "task-future-event",
            "delta",
            {
                "event": "delta",
                "choices": [{"delta": {"content": "before"}, "index": 0}],
            },
        )
        await future_events.append(
            "task-future-event",
            "future_ui_control",
            {"event": "future_ui_control", "state": "important"},
        )
        future_snapshot = await future_events.replay_snapshot("task-future-event")
        self.soft_assert_equal(
            future_snapshot.available if future_snapshot else None,
            False,
            "Unknown events should make compact replay unavailable",
        )
        self.soft_assert_equal(
            [
                event.event
                for event in await future_events.events_after("task-future-event")
            ],
            ["delta", "future_ui_control"],
            "Raw replay should preserve unknown events for a compatible browser",
        )

        known_ignored_events = ChatTaskEventBuffer(max_events_per_task=3)
        await known_ignored_events.append(
            "task-known-ignored",
            "mcp_connection_unavailable",
            {
                "event": "mcp_connection_unavailable",
                "connection_name": "Unavailable Server",
                "status": "unavailable",
            },
        )
        known_ignored_snapshot = await known_ignored_events.replay_snapshot(
            "task-known-ignored"
        )
        self.soft_assert_equal(
            known_ignored_snapshot.available if known_ignored_snapshot else None,
            True,
            "Known non-rendered events should preserve compact replay availability",
        )

        await retained.append("task-delta", "done", {})
        self.soft_assert_equal(
            await retained.events_after("task-gamma"),
            [],
            "Terminal task retention should prune older terminal task streams",
        )
        self.soft_assert(
            not await retained.has_stream("task-gamma"),
            "Pruned terminal task streams should no longer report retained state",
        )
        self.soft_assert(
            await retained.has_stream("task-delta"),
            "Newest terminal task stream should report retained state",
        )
        self.soft_assert_equal(
            [event.event for event in await retained.events_after("task-delta")],
            ["done"],
            "Newest terminal task stream should remain replayable",
        )

        self.assert_no_failures()
