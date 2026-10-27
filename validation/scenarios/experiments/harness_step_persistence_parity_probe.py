"""Probe Harness StepPersistence against AssistantMD recovery requirements."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.models.function import (  # noqa: E402
    AgentInfo,
    DeltaToolCall,
    FunctionModel,
)
from pydantic_ai_harness.step_persistence import (  # noqa: E402
    InMemoryStepStore,
    SqliteStepStore,
    StepPersistence,
)

from validation.support.base_scenario import BaseScenario  # noqa: E402


class HarnessStepPersistenceParityProbeScenario(BaseScenario):
    """Record native snapshot and effect-ledger behavior at failure boundaries."""

    async def test_scenario(self) -> None:
        results = {
            "partial_text": await _probe_partial_text(),
            "completed_tool": await _probe_completed_tool(),
            "cancelled_tool": await _probe_cancelled_tool(),
            "sqlite_round_trip": await _probe_sqlite_round_trip(
                self.artifacts_dir / "step-persistence.db"
            ),
        }

        self.soft_assert_equal(
            results["partial_text"]["snapshot_state"],
            None,
            "Harness currently leaves no snapshot when the first model stream fails",
        )
        self.soft_assert_equal(
            results["partial_text"]["snapshot_parts"],
            [],
            "The first-stream partial-text recovery gap should remain explicit",
        )
        self.soft_assert_equal(
            results["completed_tool"]["tool_effects"],
            [{"tool_name": "effect", "status": "completed"}],
            "Completed tool should have one terminal effect-ledger record",
        )
        self.soft_assert_equal(
            results["completed_tool"]["snapshot_parts"],
            [
                ["UserPromptPart"],
                ["ToolCallPart:effect:call-1"],
                ["ToolReturnPart:effect:call-1"],
                ["TextPart"],
            ],
            "Failure snapshot should retain a settled tool cycle before partial text",
        )
        self.soft_assert_equal(
            results["cancelled_tool"]["unresolved_effects"],
            [{"tool_name": "effect", "status": "started"}],
            "Cancellation during a tool should remain explicitly unresolved",
        )
        self.soft_assert_equal(
            results["sqlite_round_trip"]["snapshot_parts"],
            [
                ["UserPromptPart"],
                ["ToolCallPart:effect:call-1"],
                ["ToolReturnPart:effect:call-1"],
                ["TextPart"],
            ],
            "SQLite should round-trip native history after a settled tool boundary",
        )

        (self.artifacts_dir / "harness_step_persistence_parity.json").write_text(
            json.dumps(results, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self.teardown_scenario()
        self.assert_no_failures()


def _read_error(message: str) -> httpx.ReadError:
    return httpx.ReadError(
        message,
        request=httpx.Request("POST", "https://provider.test/v1/responses"),
    )


def _snapshot_parts(messages: list[Any]) -> list[list[str]]:
    shaped: list[list[str]] = []
    for message in messages:
        parts: list[str] = []
        for part in message.parts:
            label = type(part).__name__
            tool_name = getattr(part, "tool_name", None)
            tool_call_id = getattr(part, "tool_call_id", None)
            if tool_name is not None or tool_call_id is not None:
                label = f"{label}:{tool_name}:{tool_call_id}"
            parts.append(label)
        shaped.append(parts)
    return shaped


async def _run_disconnecting_agent(
    *, store: Any, agent_name: str, with_tool: bool
) -> tuple[str, list[str]]:
    effects: list[str] = []

    async def stream(
        messages: list[Any], _info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        tool_returned = any(
            getattr(part, "tool_name", None) == "effect"
            for message in messages
            for part in message.parts
        )
        if with_tool and not tool_returned:
            yield {
                0: DeltaToolCall(
                    name="effect",
                    json_args='{"value":"once"}',
                    tool_call_id="call-1",
                )
            }
            return
        yield "partial response"
        raise _read_error(f"{agent_name}-disconnect")

    agent = Agent(
        FunctionModel(stream_function=stream),
        capabilities=[StepPersistence(store=store, agent_name=agent_name)],
    )

    @agent.tool_plain
    async def effect(value: str) -> str:
        effects.append(value)
        return "effect-complete"

    try:
        async with agent.run_stream_events("probe persistence") as events:
            async for _ in events:
                pass
    except httpx.ReadError:
        pass
    else:
        raise AssertionError("Synthetic disconnect unexpectedly completed")

    run = (await store.list_runs())[-1]
    return run.run_id, effects


async def _probe_partial_text() -> dict[str, Any]:
    store = InMemoryStepStore()
    run_id, _effects = await _run_disconnecting_agent(
        store=store,
        agent_name="partial",
        with_tool=False,
    )
    snapshot = await store.latest_snapshot(run_id=run_id)
    return {
        "run_id": run_id,
        "snapshot_state": None if snapshot is None else snapshot.state,
        "snapshot_parts": (
            [] if snapshot is None else _snapshot_parts(snapshot.messages)
        ),
        "event_kinds": [event.kind for event in await store.list_events(run_id=run_id)],
    }


async def _probe_completed_tool() -> dict[str, Any]:
    store = InMemoryStepStore()
    run_id, effects = await _run_disconnecting_agent(
        store=store,
        agent_name="completed-tool",
        with_tool=True,
    )
    snapshot = await store.latest_snapshot(run_id=run_id)
    assert snapshot is not None
    effect = await store.get_tool_effect(run_id=run_id, tool_call_id="call-1")
    assert effect is not None
    return {
        "run_id": run_id,
        "effects": effects,
        "snapshot_state": snapshot.state,
        "snapshot_parts": _snapshot_parts(snapshot.messages),
        "tool_effects": [{"tool_name": effect.tool_name, "status": effect.status}],
    }


async def _probe_cancelled_tool() -> dict[str, Any]:
    store = InMemoryStepStore()
    tool_started = asyncio.Event()

    async def stream(
        _messages: list[Any], _info: AgentInfo
    ) -> AsyncIterator[dict[int, DeltaToolCall]]:
        yield {
            0: DeltaToolCall(
                name="effect",
                json_args='{"value":"pending"}',
                tool_call_id="call-cancelled",
            )
        }

    agent = Agent(
        FunctionModel(stream_function=stream),
        capabilities=[StepPersistence(store=store, agent_name="cancelled-tool")],
    )

    @agent.tool_plain
    async def effect(value: str) -> str:
        del value
        tool_started.set()
        await asyncio.Event().wait()
        return "unreachable"

    async def consume_events() -> None:
        async with agent.run_stream_events("probe cancellation") as events:
            async for _ in events:
                pass

    task = asyncio.create_task(consume_events())
    await asyncio.wait_for(tool_started.wait(), timeout=2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    run = (await store.list_runs())[-1]
    unresolved = await store.list_unresolved_tool_effects(run_id=run.run_id)
    return {
        "run_id": run.run_id,
        "unresolved_effects": [
            {"tool_name": effect.tool_name, "status": effect.status}
            for effect in unresolved
        ],
    }


async def _probe_sqlite_round_trip(database: Path) -> dict[str, Any]:
    store = SqliteStepStore(database=database)
    run_id, _effects = await _run_disconnecting_agent(
        store=store,
        agent_name="sqlite",
        with_tool=True,
    )
    reopened = SqliteStepStore(database=database)
    snapshot = await reopened.latest_snapshot(run_id=run_id)
    assert snapshot is not None
    return {
        "run_id": run_id,
        "snapshot_state": snapshot.state,
        "snapshot_parts": _snapshot_parts(snapshot.messages),
    }
