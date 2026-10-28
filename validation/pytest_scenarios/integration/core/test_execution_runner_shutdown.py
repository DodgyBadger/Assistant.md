"""Public task-coordinator shutdown waits for actual worker cancellation cleanup."""

from __future__ import annotations

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.task_runner_workloads import (
    _wait_for_shutdown_cleanup,
    wait_snapshot,
)

pytestmark = pytest.mark.scenario


async def test_runner_shutdown_cancels_detached_children_and_notifies_after_cleanup(
    app: Application,
) -> None:
    app.vault("ExecutionRunnerShutdownVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import (
        ExecutionTaskKind,
        ExecutionTaskSource,
        TaskCoordinator,
    )
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskRunner, ExecutionTaskSpec

    observer_states = []
    cleanup_started = asyncio.Event()
    cleanup_released = asyncio.Event()
    cleanup_finished = asyncio.Event()
    observed_task_id = ""
    # Public constructor injection keeps the observer contract and uses the
    # application's real background spawner without replacing runtime services.
    coordinator = TaskCoordinator(
        terminal_observers=[
            lambda task: (
                observer_states.append(cleanup_finished.is_set())
                if task.task_id == observed_task_id
                else None
            )
        ]
    )
    runner = ExecutionTaskRunner(
        task_coordinator=coordinator,
        background_spawner=get_runtime_context().background_spawner,
    )
    task = await runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:shutdown",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-shutdown",
            authority=SYSTEM_AUTHORITY,
        ),
        lambda snapshot: _wait_for_shutdown_cleanup(
            snapshot, cleanup_started, cleanup_released, cleanup_finished
        ),
    )
    observed_task_id = task.task_id
    child = await coordinator.create_queued_task(
        kind=ExecutionTaskKind.DELEGATE,
        scope="runner:shutdown-child",
        source=ExecutionTaskSource.TOOL,
        label="runner-detached-shutdown-child",
        authority=SYSTEM_AUTHORITY,
        parent_task_id=task.task_id,
        detached_from_parent_lifecycle=True,
    )
    await wait_snapshot(
        coordinator, task.task_id, lambda snapshot: snapshot.status == "running"
    )
    await coordinator.shutdown(reason="validation_shutdown")
    await asyncio.wait_for(cleanup_started.wait(), timeout=2)
    try:
        with pytest.raises(
            RuntimeError, match="Execution task coordinator is shutting down"
        ):
            await coordinator.create_queued_task(
                kind=ExecutionTaskKind.DELEGATE,
                scope="runner:late-shutdown-child",
                source=ExecutionTaskSource.TOOL,
                label="runner-late-shutdown-child",
                authority=SYSTEM_AUTHORITY,
                detached_from_parent_lifecycle=True,
            )
        cancelled_child = await coordinator.get_task(child.task_id)
        assert cancelled_child is not None and cancelled_child.status == "cancelled"
        assert observer_states == []
    finally:
        cleanup_released.set()
    terminal = await wait_snapshot(
        coordinator, task.task_id, lambda snapshot: snapshot.is_terminal
    )
    assert terminal.status == "cancelled"
    await coordinator.mark_unfinished_cancelled(reason="validation_shutdown")
    assert observer_states == [True]
