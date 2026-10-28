"""Real execution tasks and observable production lifecycle contracts."""

from __future__ import annotations

import asyncio

import pytest

from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_runner_revision_waits_attention_terminal_and_eviction(
    app: Application,
) -> None:
    app.vault("ExecutionTaskRunnerVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import (
        ExecutionTaskKind,
        ExecutionTaskSource,
        TaskCoordinator,
    )
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    observable_task = await runtime.task_coordinator.create_queued_task(
        kind=ExecutionTaskKind.CHAT,
        scope="runner:observable",
        source=ExecutionTaskSource.SYSTEM,
        label="runner-observable",
        authority=SYSTEM_AUTHORITY,
        metadata={"probe": "observable"},
    )
    wait_for_change = asyncio.create_task(
        runtime.task_coordinator.wait_for_tasks(
            [observable_task.task_id],
            after_revisions={observable_task.task_id: observable_task.revision},
            timeout_seconds=1.0,
        )
    )
    await asyncio.sleep(0)
    await runtime.task_coordinator.update_metadata(
        observable_task.task_id, {"progress": "advanced"}
    )
    changed = await wait_for_change
    changed_snapshot = changed.snapshots[0] if changed.snapshots else None
    assert (
        changed.timed_out is False
    ), "Coordinator wait should wake when an observed task revision changes"
    assert (
        changed_snapshot.revision > observable_task.revision
        if changed_snapshot
        else False
    ) is True, "Observable task mutations should increment the task revision"
    wait_timeout = await runtime.task_coordinator.wait_for_tasks(
        [observable_task.task_id],
        after_revisions={
            observable_task.task_id: (
                changed_snapshot.revision
                if changed_snapshot
                else observable_task.revision
            )
        },
        timeout_seconds=0.01,
    )
    assert (
        wait_timeout.timed_out is True
    ), "Coordinator wait timeout should be a normal observation outcome"
    assert (
        wait_timeout.snapshots[0].task_id if wait_timeout.snapshots else None
    ) == observable_task.task_id, (
        "Coordinator wait timeout should return the latest task snapshot"
    )
    terminal_only_wait = asyncio.create_task(
        runtime.task_coordinator.wait_for_tasks(
            [observable_task.task_id],
            timeout_seconds=1.0,
            terminal_or_attention_only=True,
            wake_on_attention=False,
        )
    )
    await asyncio.sleep(0)
    await runtime.task_coordinator.publish_progress(
        observable_task.task_id,
        metadata={"progress": "needs-review"},
        health_status="attention_required",
    )
    await asyncio.sleep(0)
    assert (
        terminal_only_wait.done() is False
    ), "Terminal-only waits should ignore nonterminal attention state"
    await runtime.task_coordinator.mark_completed(observable_task.task_id)
    terminal_only_result = await terminal_only_wait
    assert (
        terminal_only_result.snapshots[0].status == "completed"
    ), "Terminal-only waits should wake on terminal state"
    eviction_coordinator = TaskCoordinator(terminal_history_limit=1)
    evicted_task = await eviction_coordinator.create_queued_task(
        kind=ExecutionTaskKind.CHAT,
        scope="runner:evicted-wait",
        source=ExecutionTaskSource.SYSTEM,
        label="runner-evicted-wait",
        authority=SYSTEM_AUTHORITY,
    )
    eviction_wait = asyncio.create_task(
        eviction_coordinator.wait_for_tasks(
            [evicted_task.task_id],
            timeout_seconds=0.1,
            terminal_or_attention_only=True,
            wake_on_attention=False,
        )
    )
    await asyncio.sleep(0)
    await eviction_coordinator.mark_completed(evicted_task.task_id)
    churn_task = await eviction_coordinator.create_queued_task(
        kind=ExecutionTaskKind.CHAT,
        scope="runner:eviction-churn",
        source=ExecutionTaskSource.SYSTEM,
        label="runner-eviction-churn",
        authority=SYSTEM_AUTHORITY,
    )
    await eviction_coordinator.mark_completed(churn_task.task_id)
    evicted_wait_result = await eviction_wait
    assert (
        evicted_wait_result.timed_out is False
    ), "Waiters should wake when a requested terminal task is evicted"
