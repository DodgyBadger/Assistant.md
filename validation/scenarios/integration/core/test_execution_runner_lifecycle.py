"""Real execution tasks and observable production lifecycle contracts."""

from __future__ import annotations

import asyncio

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.task_runner_workloads import (
    _complete_inline_task,
    _complete_task,
    _fail_task,
    _raise_timeout_error,
    _record_failure,
    _record_timeout,
    _slow_task,
    _wait_until_cancelled,
    wait_terminal,
)

pytestmark = pytest.mark.scenario


async def test_runner_background_inline_and_terminal_start_contracts(
    app: Application,
) -> None:
    app.vault("ExecutionTaskRunnerVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskSpec

    runtime = get_runtime_context()
    completed_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:completed",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-completed",
            authority=SYSTEM_AUTHORITY,
            metadata={"probe": "completed"},
        ),
        _complete_task,
    )
    completed = await wait_terminal(runtime, completed_task.task_id)
    assert (
        completed.status if completed else None
    ) == "completed", "Runner should mark successful background work completed"
    assert (
        completed.started_at is not None if completed else False
    ) is True, "Runner should mark successful background work started"
    inline_task_ids: list[str] = []
    inline_result = await runtime.task_runner.run_inline(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:inline",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-inline",
            authority=SYSTEM_AUTHORITY,
            metadata={"probe": "inline"},
        ),
        lambda task: _complete_inline_task(task, inline_task_ids),
    )
    inline_terminal = await runtime.task_coordinator.get_task(inline_task_ids[0])
    assert (
        inline_result == "inline-complete"
    ), "Runner should return inline task results"
    assert (
        inline_terminal.status if inline_terminal else None
    ) == "completed", "Runner should mark successful inline work completed"
    try:
        await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:inline-detached",
                source=ExecutionTaskSource.SYSTEM,
                label="runner-inline-detached",
                authority=SYSTEM_AUTHORITY,
                detached_from_parent_lifecycle=True,
            ),
            _complete_task,
        )
    except ValueError as exc:
        assert (
            str(exc) == "Inline execution tasks cannot detach from their caller"
        ), "Inline detachment should fail with a stable explanation"
    else:
        raise AssertionError("Inline execution tasks must reject detachment")
    terminal_start = await runtime.task_coordinator.create_queued_task(
        kind=ExecutionTaskKind.CHAT,
        scope="runner:terminal-start",
        source=ExecutionTaskSource.SYSTEM,
        label="runner-terminal-start",
        authority=SYSTEM_AUTHORITY,
    )
    await runtime.task_coordinator.mark_cancelled(
        terminal_start.task_id, reason="validation_terminal_start"
    )
    try:
        await runtime.task_coordinator.mark_started(terminal_start.task_id)
    except RuntimeError:
        pass
    else:
        raise AssertionError("A terminal task must reject a delayed start")
    terminal_after_start = await runtime.task_coordinator.get_task(
        terminal_start.task_id
    )
    assert (
        terminal_after_start.status if terminal_after_start else None
    ) == "cancelled", "A delayed start must not resurrect a terminal task"
    app.events.require(
        "execution_task_completed", task_id=completed_task.task_id, status="completed"
    )


async def test_runner_cancellation_failure_and_timeout_hooks(app: Application) -> None:
    app.vault("ExecutionTaskRunnerVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskHooks, ExecutionTaskSpec

    runtime = get_runtime_context()
    cancel_hook_task_ids: list[str] = []
    cancel_started = asyncio.Event()
    cancel_hook_finished = asyncio.Event()

    async def record_cancelled(task_id: str) -> None:
        cancel_hook_task_ids.append(task_id)
        cancel_hook_finished.set()

    cancelled_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:cancelled",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-cancelled",
            authority=SYSTEM_AUTHORITY,
            metadata={"probe": "cancelled"},
        ),
        lambda task: _wait_until_cancelled(task, cancel_started),
        hooks=ExecutionTaskHooks(on_cancelled=record_cancelled),
    )
    await asyncio.wait_for(cancel_started.wait(), timeout=2.0)
    await runtime.task_coordinator.cancel_task(
        cancelled_task.task_id, reason="validation_cancel"
    )
    cancelled = await wait_terminal(runtime, cancelled_task.task_id)
    assert (
        cancelled.status if cancelled else None
    ) == "cancelled", "Runner should mark cancelled background work cancelled"
    await asyncio.wait_for(cancel_hook_finished.wait(), timeout=2.0)
    assert cancel_hook_task_ids == [
        cancelled_task.task_id
    ], "Runner should call cancellation hook exactly once"
    failure_hook: list[tuple[str, str]] = []
    failed_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:failed",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-failed",
            authority=SYSTEM_AUTHORITY,
            metadata={"probe": "failed"},
        ),
        _fail_task,
        hooks=ExecutionTaskHooks(
            on_failed=lambda task_id, exc: _record_failure(failure_hook, task_id, exc)
        ),
    )
    failed = await wait_terminal(runtime, failed_task.task_id)
    assert (
        failed.status if failed else None
    ) == "failed", "Runner should mark failed background work failed"
    assert (
        failed.terminal_reason if failed else ""
    ) == "RuntimeError: forced runner failure", (
        "Runner should preserve failure reason from TaskCoordinator"
    )
    assert failure_hook == [
        (failed_task.task_id, "RuntimeError")
    ], "Runner should call failure hook with the original exception"
    timeout_hook: list[tuple[str, float, str]] = []
    timed_out_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:timed-out",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-timed-out",
            authority=SYSTEM_AUTHORITY,
            metadata={"probe": "timed_out"},
            timeout_seconds=0.01,
            timeout_reason="validation_timeout",
        ),
        _slow_task,
        hooks=ExecutionTaskHooks(
            on_timed_out=lambda task_id, timeout, reason: _record_timeout(
                timeout_hook, task_id, timeout, reason
            )
        ),
    )
    timed_out = await wait_terminal(runtime, timed_out_task.task_id)
    assert (
        timed_out.status if timed_out else None
    ) == "timed_out", "Runner should mark timed-out background work timed_out"
    assert (
        timed_out.terminal_reason if timed_out else ""
    ) == "validation_timeout", "Runner should preserve configured timeout reason"
    assert timeout_hook == [
        (timed_out_task.task_id, 0.01, "validation_timeout")
    ], "Runner should call timeout hook before terminal completion"
    natural_timeout_task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:natural-timeout-error",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-natural-timeout-error",
            authority=SYSTEM_AUTHORITY,
            timeout_seconds=1.0,
        ),
        _raise_timeout_error,
    )
    natural_timeout = await wait_terminal(runtime, natural_timeout_task.task_id)
    assert (
        natural_timeout.status if natural_timeout else None
    ) == "failed", "An inner TimeoutError should remain a domain failure"


async def test_runner_immediate_cancellation_survives_failing_cancellation_hook(
    app: Application,
) -> None:
    app.vault("ExecutionRunnerImmediateCancellationVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskHooks, ExecutionTaskSpec
    from validation.scenarios.support.task_runner_workloads import (
        _raise_cancel_hook,
    )

    runtime = get_runtime_context()
    existing_workers = set(runtime.background_tasks)
    # Actual same-loop scheduling permits cancellation before attachment. Keep
    # the real spawned worker only to consume its deliberately failing hook.
    task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:pre-attachment-hook-failure",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-pre-attachment-hook-failure",
            authority=SYSTEM_AUTHORITY,
        ),
        _complete_task,
        hooks=ExecutionTaskHooks(on_cancelled=_raise_cancel_hook),
    )
    spawned_workers = set(runtime.background_tasks) - existing_workers
    await runtime.task_coordinator.cancel_task(
        task.task_id, reason="validation_pre_attachment_hook_failure"
    )
    await asyncio.gather(*spawned_workers, return_exceptions=True)
    cancelled = await wait_terminal(runtime, task.task_id)
    assert cancelled.status == "cancelled"
    assert cancelled.started_at is None


async def test_runner_late_cancellation_cannot_replace_published_failure_or_repeat_hooks(
    app: Application,
) -> None:
    app.vault("ExecutionRunnerFailureHooksVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context
    from core.runtime.task_runner import ExecutionTaskHooks, ExecutionTaskSpec
    from validation.scenarios.support.task_runner_workloads import (
        BlockingFailureHook,
    )

    runtime = get_runtime_context()
    # This public hook runs after failure publication; it does not reproduce
    # the source's patched in-progress mark_failed publication window.
    hooks = BlockingFailureHook()
    task = await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:background-failure-cancel",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-background-failure-cancel",
            authority=SYSTEM_AUTHORITY,
        ),
        hooks.fail,
        hooks=ExecutionTaskHooks(
            on_failed=hooks.on_failed, on_cancelled=hooks.on_cancelled
        ),
    )
    await asyncio.wait_for(hooks.entered.wait(), timeout=2)
    try:
        cancel = await runtime.task_coordinator.cancel_task(
            task.task_id, reason="validation_failure_cancel"
        )
        assert cancel is not None and cancel.effective is False
        failed = await wait_terminal(runtime, task.task_id)
        assert failed.status == "failed"
        assert failed.terminal_reason == "RuntimeError: forced terminalization race"
    finally:
        hooks.release.set()
    await asyncio.wait_for(hooks.finished.wait(), timeout=2)
    assert hooks.failure_hooks == [(task.task_id, "RuntimeError")]
    assert hooks.cancellation_hooks == []
