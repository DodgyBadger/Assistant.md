"""Validate the generic execution task runner shell."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import SYSTEM_AUTHORITY
from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
from core.runtime.task_runner import (
    ExecutionGatePolicy,
    ExecutionTaskHooks,
    ExecutionTaskSpec,
)
from validation.core.base_scenario import BaseScenario


class ExecutionTaskRunnerScenario(BaseScenario):
    """Validate background runner task creation, cancellation, and failure."""

    async def test_scenario(self):
        self.create_vault("ExecutionTaskRunnerVault")
        await self.start_system()
        runtime = self._runtime()

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
        completed = await self._wait_for_task_terminal(completed_task.task_id)
        self.soft_assert_equal(
            completed.status if completed else None,
            "completed",
            "Runner should mark successful background work completed",
        )
        self.soft_assert_equal(
            completed.started_at is not None if completed else False,
            True,
            "Runner should mark successful background work started",
        )

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
        self.soft_assert_equal(
            inline_result,
            "inline-complete",
            "Runner should return inline task results",
        )
        self.soft_assert_equal(
            inline_terminal.status if inline_terminal else None,
            "completed",
            "Runner should mark successful inline work completed",
        )

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
                after_revisions={
                    observable_task.task_id: observable_task.revision,
                },
                timeout_seconds=1.0,
            )
        )
        await asyncio.sleep(0)
        await runtime.task_coordinator.update_metadata(
            observable_task.task_id,
            {"progress": "advanced"},
        )
        changed = await wait_for_change
        changed_snapshot = changed.snapshots[0] if changed.snapshots else None
        self.soft_assert_equal(
            changed.timed_out,
            False,
            "Coordinator wait should wake when an observed task revision changes",
        )
        self.soft_assert_equal(
            (
                changed_snapshot.revision > observable_task.revision
                if changed_snapshot
                else False
            ),
            True,
            "Observable task mutations should increment the task revision",
        )

        wait_timeout = await runtime.task_coordinator.wait_for_tasks(
            [observable_task.task_id],
            after_revisions={
                observable_task.task_id: (
                    changed_snapshot.revision
                    if changed_snapshot
                    else observable_task.revision
                ),
            },
            timeout_seconds=0.01,
        )
        self.soft_assert_equal(
            wait_timeout.timed_out,
            True,
            "Coordinator wait timeout should be a normal observation outcome",
        )
        self.soft_assert_equal(
            wait_timeout.snapshots[0].task_id if wait_timeout.snapshots else None,
            observable_task.task_id,
            "Coordinator wait timeout should return the latest task snapshot",
        )

        terminal_result_task = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:terminal-result",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-terminal-result",
            authority=SYSTEM_AUTHORITY,
        )
        oversized_text = "x" * 70_000
        await runtime.task_coordinator.record_result(
            terminal_result_task.task_id,
            {
                "text": oversized_text,
                "artifact_references": ["vault://report.md"],
            },
        )
        await runtime.task_coordinator.mark_completed(terminal_result_task.task_id)
        terminal_result = await runtime.task_coordinator.wait_for_tasks(
            [terminal_result_task.task_id],
            after_revisions={terminal_result_task.task_id: 0},
            timeout_seconds=1.0,
            terminal_or_attention_only=True,
        )
        result_snapshot = (
            terminal_result.snapshots[0] if terminal_result.snapshots else None
        )
        self.soft_assert_equal(
            result_snapshot.status if result_snapshot else None,
            "completed",
            "Coordinator wait should return already-terminal tasks immediately",
        )
        self.soft_assert_equal(
            result_snapshot.result_truncated if result_snapshot else False,
            True,
            "Execution task terminal results should be bounded",
        )
        self.soft_assert_equal(
            (
                result_snapshot.result.get("artifact_references")
                if result_snapshot and result_snapshot.result
                else None
            ),
            ["vault://report.md"],
            "Result truncation should preserve artifact references",
        )

        cancel_hook_task_ids: list[str] = []
        cancel_started = asyncio.Event()
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
            hooks=ExecutionTaskHooks(
                on_cancelled=lambda task_id: _record_task_id(
                    cancel_hook_task_ids, task_id
                ),
            ),
        )
        await asyncio.wait_for(cancel_started.wait(), timeout=2.0)
        await runtime.task_coordinator.cancel_task(
            cancelled_task.task_id, reason="validation_cancel"
        )
        cancelled = await self._wait_for_task_terminal(cancelled_task.task_id)
        self.soft_assert_equal(
            cancelled.status if cancelled else None,
            "cancelled",
            "Runner should mark cancelled background work cancelled",
        )
        self.soft_assert_equal(
            cancel_hook_task_ids,
            [cancelled_task.task_id],
            "Runner should call cancellation hook exactly once",
        )

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
                on_failed=lambda task_id, exc: _record_failure(
                    failure_hook, task_id, exc
                ),
            ),
        )
        failed = await self._wait_for_task_terminal(failed_task.task_id)
        self.soft_assert_equal(
            failed.status if failed else None,
            "failed",
            "Runner should mark failed background work failed",
        )
        self.soft_assert_equal(
            failed.terminal_reason if failed else "",
            "RuntimeError: forced runner failure",
            "Runner should preserve failure reason from TaskCoordinator",
        )
        self.soft_assert_equal(
            failure_hook,
            [(failed_task.task_id, "RuntimeError")],
            "Runner should call failure hook with the original exception",
        )

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
                    timeout_hook,
                    task_id,
                    timeout,
                    reason,
                ),
            ),
        )
        timed_out = await self._wait_for_task_terminal(timed_out_task.task_id)
        self.soft_assert_equal(
            timed_out.status if timed_out else None,
            "timed_out",
            "Runner should mark timed-out background work timed_out",
        )
        self.soft_assert_equal(
            timed_out.terminal_reason if timed_out else "",
            "validation_timeout",
            "Runner should preserve configured timeout reason",
        )
        self.soft_assert_equal(
            timeout_hook,
            [(timed_out_task.task_id, 0.01, "validation_timeout")],
            "Runner should call timeout hook before terminal completion",
        )

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
                task,
                gate_policy,
                lambda: _record_gate_entry("second", gate_order),
            ),
        )
        queued_second = await self._wait_for_task_metadata(
            second_gate_task.task_id,
            "gate_reason",
            "validation_gate_active",
        )
        self.soft_assert_equal(
            queued_second.heartbeat_status if queued_second else None,
            "queued_for_gate",
            "Runner gate should heartbeat waiting tasks with the configured status",
        )
        self.soft_assert_equal(
            queued_second.metadata.get("queue_position") if queued_second else None,
            1,
            "Runner gate should record queue position for waiting tasks",
        )
        self.soft_assert_equal(
            (
                queued_second.metadata.get("waiting_for_task_id")
                if queued_second
                else None
            ),
            first_gate_task.task_id,
            "Runner gate should identify the holder task while waiting",
        )
        release_gate.set()
        first_gate_terminal = await self._wait_for_task_terminal(
            first_gate_task.task_id
        )
        second_gate_terminal = await self._wait_for_task_terminal(
            second_gate_task.task_id
        )
        self.soft_assert_equal(
            first_gate_terminal.status if first_gate_terminal else None,
            "completed",
            "First gated runner task should complete",
        )
        self.soft_assert_equal(
            second_gate_terminal.status if second_gate_terminal else None,
            "completed",
            "Second gated runner task should complete after gate release",
        )
        self.soft_assert_equal(
            gate_order,
            ["first", "second"],
            "Runner gate should serialize same-key tasks",
        )

        observer_states: list[bool] = []
        cleanup_started = asyncio.Event()
        cleanup_released = asyncio.Event()
        cleanup_finished = asyncio.Event()
        observed_task_id = ""

        runtime.task_coordinator._terminal_observers.append(  # noqa: SLF001
            lambda task: (
                observer_states.append(cleanup_finished.is_set())
                if task.task_id == observed_task_id
                else None
            )
        )
        shutdown_task = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:shutdown",
                source=ExecutionTaskSource.SYSTEM,
                label="runner-shutdown",
                authority=SYSTEM_AUTHORITY,
                metadata={"probe": "shutdown"},
            ),
            lambda task: _wait_for_shutdown_cleanup(
                task,
                cleanup_started,
                cleanup_released,
                cleanup_finished,
            ),
        )
        observed_task_id = shutdown_task.task_id
        await self._wait_for_task_status(shutdown_task.task_id, "running")
        await runtime.task_coordinator.shutdown(reason="validation_shutdown")
        await asyncio.wait_for(cleanup_started.wait(), timeout=2.0)
        self.soft_assert_equal(
            observer_states,
            [],
            "Shutdown should not mark a live task terminal before cancellation cleanup finishes",
        )
        cleanup_released.set()
        if runtime.background_tasks:
            await asyncio.gather(
                *list(runtime.background_tasks), return_exceptions=True
            )
        await runtime.task_coordinator.mark_unfinished_cancelled(
            reason="validation_shutdown"
        )
        self.soft_assert_equal(
            observer_states,
            [True],
            "Shutdown should notify terminal observers after the worker has unwound",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    async def _wait_for_task_terminal(self, task_id: str):
        runtime = self._runtime()
        for _ in range(100):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.is_terminal:
                return task
            await asyncio.sleep(0.02)
        return None

    async def _wait_for_task_metadata(self, task_id: str, key: str, value):
        runtime = self._runtime()
        for _ in range(100):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.metadata.get(key) == value:
                return task
            await asyncio.sleep(0.02)
        return None

    async def _wait_for_task_status(self, task_id: str, status: str):
        runtime = self._runtime()
        for _ in range(100):
            task = await runtime.task_coordinator.get_task(task_id)
            if task is not None and task.status == status:
                return task
            await asyncio.sleep(0.02)
        return None

    def _runtime(self):
        from core.runtime.state import get_runtime_context

        return get_runtime_context()


async def _complete_task(_task) -> None:
    await asyncio.sleep(0)


async def _complete_inline_task(task, task_ids: list[str]) -> str:
    task_ids.append(task.task_id)
    await asyncio.sleep(0)
    return "inline-complete"


async def _wait_until_cancelled(_task, started: asyncio.Event) -> None:
    started.set()
    await asyncio.Event().wait()


async def _wait_for_shutdown_cleanup(
    _task,
    cleanup_started: asyncio.Event,
    cleanup_released: asyncio.Event,
    cleanup_finished: asyncio.Event,
) -> None:
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        cleanup_started.set()
        await cleanup_released.wait()
        cleanup_finished.set()
        raise


async def _fail_task(_task) -> None:
    raise RuntimeError("forced runner failure")


async def _slow_task(_task) -> None:
    await asyncio.sleep(1)


async def _record_task_id(task_ids: list[str], task_id: str) -> None:
    task_ids.append(task_id)


async def _record_failure(
    failures: list[tuple[str, str]],
    task_id: str,
    exc: BaseException,
) -> None:
    failures.append((task_id, type(exc).__name__))


async def _record_timeout(
    timeouts: list[tuple[str, float, str]],
    task_id: str,
    timeout_seconds: float,
    reason: str,
) -> None:
    timeouts.append((task_id, timeout_seconds, reason))


async def _hold_gate(
    label: str,
    gate_order: list[str],
    entered: asyncio.Event,
    release: asyncio.Event,
) -> None:
    gate_order.append(label)
    entered.set()
    await release.wait()


async def _record_gate_entry(label: str, gate_order: list[str]) -> None:
    gate_order.append(label)
