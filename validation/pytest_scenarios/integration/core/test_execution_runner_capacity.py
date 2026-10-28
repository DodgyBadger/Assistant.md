"""Real execution tasks and observable production lifecycle contracts."""

from __future__ import annotations

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.task_runner_workloads import (
    _hold_capacity,
    _hold_concurrency_slot,
    _hold_dynamic_capacity,
    _hold_gate,
    _mark_capacity_entered,
    _record_gate_entry,
    block_cancelled_waiter,
    wait_metadata,
    wait_terminal,
)

pytestmark = pytest.mark.scenario


async def test_runner_gate_serializes_and_reports_queue_holder(
    app: Application,
) -> None:
    app.vault("ExecutionTaskRunnerVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionGatePolicy, ExecutionTaskSpec

    runtime = get_runtime_context()
    gate_entered = asyncio.Event()
    release_gate = asyncio.Event()
    gate_order: list[str] = []
    gate_policy = ExecutionGatePolicy(
        key="runner:gate",
        queued_status="queued_for_gate",
        queued_metadata={"gate_reason": "validation_gate_active"},
        clear_metadata={"gate_reason": None},
    )
    first_gate_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:gate",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-gate-first",
            authority=SYSTEM_AUTHORITY,
            metadata={"probe": "gate_first"},
        ),
        lambda task: runtime.task_runner.run_with_gate(
            task,
            gate_policy,
            lambda: _hold_gate("first", gate_order, gate_entered, release_gate),
        ),
    )
    await asyncio.wait_for(gate_entered.wait(), timeout=2.0)
    second_gate_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:gate",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-gate-second",
            authority=SYSTEM_AUTHORITY,
            metadata={"probe": "gate_second"},
        ),
        lambda task: runtime.task_runner.run_with_gate(
            task, gate_policy, lambda: _record_gate_entry("second", gate_order)
        ),
    )
    queued_second = await wait_metadata(
        runtime, second_gate_task.task_id, "gate_reason", "validation_gate_active"
    )
    assert (
        queued_second.heartbeat_status if queued_second else None
    ) == "queued_for_gate", (
        "Runner gate should heartbeat waiting tasks with the configured status"
    )
    assert (
        queued_second.metadata.get("queue_position") if queued_second else None
    ) == 1, "Runner gate should record queue position for waiting tasks"
    assert (
        queued_second.metadata.get("waiting_for_task_id") if queued_second else None
    ) == first_gate_task.task_id, (
        "Runner gate should identify the holder task while waiting"
    )
    release_gate.set()
    first_gate_terminal = await wait_terminal(runtime, first_gate_task.task_id)
    second_gate_terminal = await wait_terminal(runtime, second_gate_task.task_id)
    assert (
        first_gate_terminal.status if first_gate_terminal else None
    ) == "completed", "First gated runner task should complete"
    assert (
        second_gate_terminal.status if second_gate_terminal else None
    ) == "completed", "Second gated runner task should complete after gate release"
    assert gate_order == [
        "first",
        "second",
    ], "Runner gate should serialize same-key tasks"


async def test_runner_capacity_cancellation_and_live_queue_positions(
    app: Application,
) -> None:
    app.vault("ExecutionTaskRunnerVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionConcurrencyPolicy, ExecutionTaskSpec

    runtime = get_runtime_context()
    concurrency_entered = asyncio.Event()
    concurrency_release = asyncio.Event()
    concurrency_state = {"active": 0, "maximum": 0, "entered": 0}
    concurrency_policy = ExecutionConcurrencyPolicy(
        key="runner:concurrency",
        limit=2,
        queued_status="queued_for_capacity",
        queued_metadata={"queue_reason": "validation_capacity"},
        clear_metadata={"queue_reason": None, "queue_position": None},
    )
    concurrency_tasks = [
        await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:concurrency",
                source=ExecutionTaskSource.SYSTEM,
                label=f"runner-concurrency-{index}",
                authority=SYSTEM_AUTHORITY,
            ),
            lambda task: runtime.task_runner.run_with_concurrency(
                task,
                concurrency_policy,
                lambda: _hold_concurrency_slot(
                    concurrency_state, concurrency_entered, concurrency_release
                ),
            ),
            start_immediately=False,
        )
        for index in range(3)
    ]
    await asyncio.wait_for(concurrency_entered.wait(), timeout=2.0)
    queued_concurrency = await wait_metadata(
        runtime, concurrency_tasks[2].task_id, "queue_reason", "validation_capacity"
    )
    assert (
        queued_concurrency.status if queued_concurrency else None
    ) == "queued", "Capacity-delayed tasks should remain queued until admitted"
    assert (
        queued_concurrency.metadata.get("queue_position")
        if queued_concurrency
        else None
    ) == 1, "Capacity-delayed tasks should expose their queue position"
    concurrency_release.set()
    for task in concurrency_tasks:
        await wait_terminal(runtime, task.task_id)
    assert (
        concurrency_state["maximum"] == 2
    ), "Runner concurrency lanes should enforce the configured limit"
    assert (
        concurrency_state["entered"] == 3
    ), "Queued capacity work should run after a slot is released"
    single_entered = asyncio.Event()
    single_release = asyncio.Event()
    queued_callback_entered = asyncio.Event()
    third_entered = asyncio.Event()
    cancellation_policy = ExecutionConcurrencyPolicy(
        key="runner:queued-cancellation",
        limit=1,
        queued_status="queued_for_capacity",
        queued_metadata={"queue_reason": "validation_capacity"},
        on_queued=block_cancelled_waiter(queued_callback_entered),
    )
    first_capacity_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:queued-cancellation",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-capacity-holder",
            authority=SYSTEM_AUTHORITY,
        ),
        lambda task: runtime.task_runner.run_with_concurrency(
            task,
            cancellation_policy,
            lambda: _hold_capacity(single_entered, single_release),
        ),
        start_immediately=False,
    )
    await asyncio.wait_for(single_entered.wait(), timeout=2.0)
    cancelled_waiter = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:queued-cancellation",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-cancelled-waiter",
            authority=SYSTEM_AUTHORITY,
        ),
        lambda task: runtime.task_runner.run_with_concurrency(
            task, cancellation_policy, lambda: _mark_capacity_entered(third_entered)
        ),
        start_immediately=False,
    )
    await asyncio.wait_for(queued_callback_entered.wait(), timeout=2.0)
    await runtime.task_coordinator.cancel_task(
        cancelled_waiter.task_id, reason="validation_cancel_queued_waiter"
    )
    await wait_terminal(runtime, cancelled_waiter.task_id)
    successor = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:queued-cancellation",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-capacity-successor",
            authority=SYSTEM_AUTHORITY,
        ),
        lambda task: runtime.task_runner.run_with_concurrency(
            task, cancellation_policy, lambda: _mark_capacity_entered(third_entered)
        ),
        start_immediately=False,
    )
    single_release.set()
    await asyncio.wait_for(third_entered.wait(), timeout=2.0)
    await wait_terminal(runtime, first_capacity_task.task_id)
    successor_terminal = await wait_terminal(runtime, successor.task_id)
    assert (
        successor_terminal.status if successor_terminal else None
    ) == "completed", "Cancelling during queue publication should not leak capacity"
    live_queue_release = asyncio.Event()
    live_queue_entered = asyncio.Event()
    live_queue_policy = ExecutionConcurrencyPolicy(
        key="runner:live-queue",
        limit=1,
        queued_status="queued_for_capacity",
        queued_metadata={"queue_reason": "live_queue_capacity"},
    )
    live_holder = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:live-queue",
            source=ExecutionTaskSource.SYSTEM,
            label="live-queue-holder",
            authority=SYSTEM_AUTHORITY,
        ),
        lambda task: runtime.task_runner.run_with_concurrency(
            task,
            live_queue_policy,
            lambda: _hold_capacity(live_queue_entered, live_queue_release),
        ),
        start_immediately=False,
    )
    await asyncio.wait_for(live_queue_entered.wait(), timeout=2.0)

    async def _start_live_waiter(label: str):
        return await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:live-queue",
                source=ExecutionTaskSource.SYSTEM,
                label=label,
                authority=SYSTEM_AUTHORITY,
            ),
            lambda task: runtime.task_runner.run_with_concurrency(
                task, live_queue_policy, lambda: asyncio.sleep(0)
            ),
            start_immediately=False,
        )

    live_waiter_one = await _start_live_waiter("live-queue-one")
    live_waiter_two = await _start_live_waiter("live-queue-two")
    await wait_metadata(runtime, live_waiter_two.task_id, "queue_position", 2)
    await runtime.task_coordinator.cancel_task(
        live_waiter_one.task_id, reason="validation_live_queue_cancel"
    )
    await wait_terminal(runtime, live_waiter_one.task_id)
    promoted_position = await wait_metadata(
        runtime, live_waiter_two.task_id, "queue_position", 1
    )
    assert (
        promoted_position.metadata.get("queue_position") if promoted_position else None
    ) == 1, "Remaining capacity waiters should expose a live queue position"
    live_queue_release.set()
    await wait_terminal(runtime, live_holder.task_id)
    await wait_terminal(runtime, live_waiter_two.task_id)


async def test_runner_dynamic_limits_share_existing_capacity(app: Application) -> None:
    app.vault("ExecutionTaskRunnerVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionConcurrencyPolicy, ExecutionTaskSpec

    runtime = get_runtime_context()
    dynamic_release = asyncio.Event()
    dynamic_two_active = asyncio.Event()
    dynamic_three_active = asyncio.Event()
    dynamic_state = {"active": 0, "maximum": 0}
    dynamic_policy_one = ExecutionConcurrencyPolicy(
        key="runner:dynamic-concurrency",
        limit=1,
        queued_status="queued_for_capacity",
        queued_metadata={"queue_reason": "dynamic_capacity"},
    )
    dynamic_policy_two = ExecutionConcurrencyPolicy(
        key="runner:dynamic-concurrency",
        limit=2,
        queued_status="queued_for_capacity",
        queued_metadata={"queue_reason": "dynamic_capacity"},
    )

    async def _start_dynamic(label: str, policy: ExecutionConcurrencyPolicy):
        return await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:dynamic-concurrency",
                source=ExecutionTaskSource.SYSTEM,
                label=label,
                authority=SYSTEM_AUTHORITY,
            ),
            lambda task: runtime.task_runner.run_with_concurrency(
                task,
                policy,
                lambda: _hold_dynamic_capacity(
                    dynamic_state,
                    dynamic_two_active,
                    dynamic_three_active,
                    dynamic_release,
                ),
            ),
            start_immediately=False,
        )

    dynamic_tasks = [await _start_dynamic("dynamic-one", dynamic_policy_one)]
    async with asyncio.timeout(2):
        while dynamic_state["active"] < 1:
            await asyncio.sleep(0.01)
    dynamic_tasks.append(await _start_dynamic("dynamic-two", dynamic_policy_one))
    await wait_metadata(
        runtime, dynamic_tasks[1].task_id, "queue_reason", "dynamic_capacity"
    )
    dynamic_tasks.extend(
        [
            await _start_dynamic("dynamic-three", dynamic_policy_two),
            await _start_dynamic("dynamic-four", dynamic_policy_two),
        ]
    )
    await asyncio.wait_for(dynamic_two_active.wait(), timeout=2.0)
    await asyncio.sleep(0.02)
    assert (
        dynamic_three_active.is_set() is False
    ), "Changing a concurrency limit should not create an independent lane"
    dynamic_release.set()
    for task in dynamic_tasks:
        await wait_terminal(runtime, task.task_id)
    assert (
        dynamic_state["maximum"] == 2
    ), "A live concurrency setting change should preserve one process-wide cap"
    transition_release = asyncio.Event()
    transition_two_active = asyncio.Event()
    transition_state = {"active": 0, "maximum": 0}
    unlimited_policy = ExecutionConcurrencyPolicy(
        key="runner:unlimited-transition", limit=0, queued_status="queued_for_capacity"
    )
    limited_policy = ExecutionConcurrencyPolicy(
        key="runner:unlimited-transition", limit=1, queued_status="queued_for_capacity"
    )

    async def _start_transition(label: str, policy: ExecutionConcurrencyPolicy):
        return await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:unlimited-transition",
                source=ExecutionTaskSource.SYSTEM,
                label=label,
                authority=SYSTEM_AUTHORITY,
            ),
            lambda task: runtime.task_runner.run_with_concurrency(
                task,
                policy,
                lambda: _hold_dynamic_capacity(
                    transition_state,
                    transition_two_active,
                    asyncio.Event(),
                    transition_release,
                ),
            ),
            start_immediately=False,
        )

    transition_tasks = [
        await _start_transition("unlimited-one", unlimited_policy),
        await _start_transition("unlimited-two", unlimited_policy),
    ]
    await asyncio.wait_for(transition_two_active.wait(), timeout=2.0)
    limited_entered = asyncio.Event()
    limited_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:unlimited-transition",
            source=ExecutionTaskSource.SYSTEM,
            label="limited-after-unlimited",
            authority=SYSTEM_AUTHORITY,
        ),
        lambda task: runtime.task_runner.run_with_concurrency(
            task, limited_policy, lambda: _mark_capacity_entered(limited_entered)
        ),
        start_immediately=False,
    )
    await asyncio.sleep(0.02)
    assert (
        limited_entered.is_set() is False
    ), "A 0-to-1 limit change should account for already-active holders"
    transition_release.set()
    await asyncio.wait_for(limited_entered.wait(), timeout=2.0)
    for task in [*transition_tasks, limited_task]:
        await wait_terminal(runtime, task.task_id)
