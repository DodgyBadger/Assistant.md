"""Public buffer replay, projection, wakeup, and retention contracts."""

import asyncio

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_chat_task_event_buffer(app: Application) -> None:
    await app.start()
    from core.chat.task_events import ChatTaskEventBuffer, ChatTaskEventCursorExpired

    buffer = ChatTaskEventBuffer(max_events_per_task=3, max_terminal_tasks=1)
    first = await buffer.append("alpha", "delta", {"text": "hello"})
    second = await buffer.append(
        "alpha", "tool_call_started", {"tool_name": "delegate"}
    )
    replay = await buffer.events_after("alpha", after_sequence=0)
    assert [event.sequence for event in replay] == [first.sequence, second.sequence]
    assert replay[0].data == {"text": "hello"}

    async def collect():
        return [event async for event in buffer.subscribe("beta")]

    subscriber = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await buffer.append("beta", "delta", {"text": "wake"})
    await buffer.append("beta", "done", {"finish_reason": "stop"})
    assert [event.event for event in await asyncio.wait_for(subscriber, 1)] == [
        "delta",
        "done",
    ]
    assert await buffer.is_terminal("beta")
    assert [event async for event in buffer.subscribe("beta", after_sequence=2)] == []

    retained = ChatTaskEventBuffer(max_events_per_task=2, max_terminal_tasks=1)
    for index, kind in ((1, "delta"), (2, "delta"), (3, "done")):
        await retained.append("gamma", kind, {"index": index})
    assert [
        event.sequence
        for event in await retained.events_after("gamma", after_sequence=1)
    ] == [2, 3]
    with pytest.raises(ChatTaskEventCursorExpired) as expired:
        await retained.events_after("gamma", after_sequence=0)
    assert expired.value.oldest_available_sequence == 2
    assert expired.value.latest_sequence == 3
    await retained.append("new", "done", {})
    assert await retained.events_after("gamma") == []
    assert not await retained.has_stream("gamma")
    assert await retained.has_stream("new")
    assert [event.event for event in await retained.events_after("new")] == ["done"]

    projection = ChatTaskEventBuffer(max_events_per_task=2, max_terminal_tasks=2)
    for text in ("think ", "more"):
        await projection.append(
            "p",
            "thinking_delta",
            {"event": "thinking_delta", "delta": {"content": text}},
        )
    for text in ("hello ", "world"):
        await projection.append(
            "p",
            "delta",
            {"event": "delta", "choices": [{"delta": {"content": text}, "index": 0}]},
        )
    await projection.append(
        "p",
        "tool_call_started",
        {
            "event": "tool_call_started",
            "tool_call_id": "tool-1",
            "tool_name": "read_file",
        },
    )
    snapshot = await projection.replay_snapshot("p")
    assert snapshot is not None and snapshot.latest_sequence == 5
    assert [event.event for event in snapshot.events] == [
        "thinking_delta",
        "delta",
        "tool_call_started",
    ]
    assert snapshot.events[0].data["delta"]["content"] == "think more"
    assert snapshot.events[1].data["choices"][0]["delta"]["content"] == "hello world"
    await projection.append(
        "p",
        "tool_call_started",
        {"event": "tool_call_started", "tool_call_id": "tool-2", "tool_name": "search"},
    )
    await projection.append(
        "p",
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
        "p",
        "chat_retry_scheduled",
        {"event": "chat_retry_scheduled", "reset_response": True},
    )
    await projection.append(
        "p",
        "delta",
        {
            "event": "delta",
            "choices": [{"delta": {"content": "replacement"}, "index": 0}],
        },
    )
    await projection.append(
        "p", "review_required", {"event": "review_required", "artifact_ref": "review-1"}
    )
    snapshot = await projection.replay_snapshot("p")
    assert snapshot is not None and snapshot.latest_sequence == 10
    assert [event.event for event in snapshot.events] == [
        "tool_call_started",
        "tool_call_started",
        "tool_call_finished",
        "delta",
        "review_required",
    ]
    assert [
        event.data.get("tool_call_id")
        for event in snapshot.events
        if event.event == "tool_call_started"
    ] == ["tool-1", "tool-2"]
    assert snapshot.events[-2].data["choices"][0]["delta"]["content"] == "replacement"
    assert all(
        "arguments" not in event.data and "result" not in event.data
        for event in snapshot.events
    )
    await projection.append(
        "p",
        "done",
        {
            "event": "done",
            "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
        },
    )
    assert [
        event.event
        for event in await projection.events_after(
            "p", after_sequence=snapshot.latest_sequence
        )
    ] == ["done"]
    terminal = await projection.replay_snapshot("p")
    assert terminal is not None and terminal.events[-1].event == "done"
    assert await projection.replay_snapshot("missing") is None

    redirect = ChatTaskEventBuffer(max_events_per_task=1)
    await redirect.append(
        "r",
        "delta",
        {
            "event": "delta",
            "choices": [{"delta": {"content": "discard me"}, "index": 0}],
        },
    )
    await redirect.append(
        "r",
        "chat_retry_redirect",
        {
            "event": "chat_retry_redirect",
            "replacement_task_id": "replacement-task",
            "reset_response": True,
        },
    )
    redirected = await redirect.replay_snapshot("r")
    assert redirected is not None and [event.event for event in redirected.events] == [
        "chat_retry_redirect"
    ]

    future = ChatTaskEventBuffer(max_events_per_task=3)
    await future.append(
        "f",
        "delta",
        {"event": "delta", "choices": [{"delta": {"content": "before"}, "index": 0}]},
    )
    await future.append(
        "f", "future_ui_control", {"event": "future_ui_control", "state": "important"}
    )
    future_snapshot = await future.replay_snapshot("f")
    assert future_snapshot is not None and future_snapshot.available is False
    assert [event.event for event in await future.events_after("f")] == [
        "delta",
        "future_ui_control",
    ]
    ignored = ChatTaskEventBuffer(max_events_per_task=3)
    await ignored.append(
        "i",
        "mcp_connection_unavailable",
        {
            "event": "mcp_connection_unavailable",
            "connection_name": "Unavailable Server",
            "status": "unavailable",
        },
    )
    ignored_snapshot = await ignored.replay_snapshot("i")
    assert ignored_snapshot is not None and ignored_snapshot.available is True
