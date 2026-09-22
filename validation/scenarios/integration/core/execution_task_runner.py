"""Validate the generic execution task runner shell."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import SYSTEM_AUTHORITY, ExecutionAuthority
from core.runtime.execution_tasks import (
    EXECUTION_TASK_RESULT_MAX_CHARS,
    ExecutionTaskKind,
    ExecutionTaskSource,
    TaskCoordinator,
)
from core.runtime.task_runner import (
    ExecutionConcurrencyPolicy,
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

        captured_spawns = []
        original_spawn = runtime.background_spawner.spawn
        runtime.background_spawner.spawn = captured_spawns.append
        try:
            cancelled_before_attachment = await runtime.task_runner.start_background(
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
        finally:
            runtime.background_spawner.spawn = original_spawn
        await runtime.task_coordinator.cancel_task(
            cancelled_before_attachment.task_id,
            reason="validation_pre_attachment_hook_failure",
        )
        captured_worker = asyncio.create_task(captured_spawns.pop()())
        await asyncio.gather(captured_worker, return_exceptions=True)
        cancelled_after_hook_failure = await runtime.task_coordinator.get_task(
            cancelled_before_attachment.task_id
        )
        self.soft_assert_equal(
            (
                cancelled_after_hook_failure.status
                if cancelled_after_hook_failure
                else None
            ),
            "cancelled",
            "A failed cancellation hook should not strand a pre-attachment task",
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
            self.soft_assert_equal(
                str(exc),
                "Inline execution tasks cannot detach from their caller",
                "Inline detachment should fail with a stable explanation",
            )
        else:
            self.soft_assert(False, "Inline execution tasks must reject detachment")

        inline_start_entered = asyncio.Event()
        release_inline_start = asyncio.Event()
        original_mark_started = runtime.task_coordinator.mark_started

        async def _delay_inline_start(task_id):
            inline_start_entered.set()
            await release_inline_start.wait()
            await original_mark_started(task_id)

        runtime.task_coordinator.mark_started = _delay_inline_start
        try:
            interrupted_inline = asyncio.create_task(
                runtime.task_runner.run_inline(
                    ExecutionTaskSpec(
                        kind=ExecutionTaskKind.CHAT,
                        scope="runner:interrupted-inline-start",
                        source=ExecutionTaskSource.SYSTEM,
                        label="runner-interrupted-inline-start",
                        authority=SYSTEM_AUTHORITY,
                    ),
                    _complete_task,
                )
            )
            await asyncio.wait_for(inline_start_entered.wait(), timeout=1.0)
            interrupted_inline.cancel()
            try:
                await interrupted_inline
            except asyncio.CancelledError:
                pass
            else:
                self.soft_assert(False, "Interrupted inline start should cancel")
        finally:
            runtime.task_coordinator.mark_started = original_mark_started
            release_inline_start.set()
        interrupted_inline_records = await runtime.task_coordinator.list_tasks(
            scope="runner:interrupted-inline-start"
        )
        self.soft_assert_equal(
            [task.status for task in interrupted_inline_records],
            ["cancelled"],
            "Interrupted inline startup should not strand a queued task",
        )

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
            self.soft_assert(False, "A terminal task must reject a delayed start")
        terminal_after_start = await runtime.task_coordinator.get_task(
            terminal_start.task_id
        )
        self.soft_assert_equal(
            terminal_after_start.status if terminal_after_start else None,
            "cancelled",
            "A delayed start must not resurrect a terminal task",
        )

        completion_entered: dict[str, asyncio.Event] = {
            "runner:inline-completion-cancel": asyncio.Event(),
            "runner:background-completion-cancel": asyncio.Event(),
        }
        hold_completion = asyncio.Event()
        original_mark_completed = runtime.task_coordinator.mark_completed

        async def _delay_task_completion(task_id, *, reason=None):
            snapshot = await runtime.task_coordinator.get_task(task_id)
            scope = snapshot.scope if snapshot else ""
            if scope in completion_entered:
                completion_entered[scope].set()
                await hold_completion.wait()
            await original_mark_completed(task_id, reason=reason)

        runtime.task_coordinator.mark_completed = _delay_task_completion
        try:
            completing_inline = asyncio.create_task(
                runtime.task_runner.run_inline(
                    ExecutionTaskSpec(
                        kind=ExecutionTaskKind.CHAT,
                        scope="runner:inline-completion-cancel",
                        source=ExecutionTaskSource.SYSTEM,
                        label="runner-inline-completion-cancel",
                        authority=SYSTEM_AUTHORITY,
                    ),
                    _complete_task,
                )
            )
            await asyncio.wait_for(
                completion_entered["runner:inline-completion-cancel"].wait(),
                timeout=1.0,
            )
            completing_inline.cancel()
            try:
                await completing_inline
            except asyncio.CancelledError:
                pass
            else:
                self.soft_assert(False, "Inline completion cancellation should cancel")
            inline_completion_records = await runtime.task_coordinator.list_tasks(
                scope="runner:inline-completion-cancel"
            )
            self.soft_assert_equal(
                [task.status for task in inline_completion_records],
                ["cancelled"],
                "Cancellation during inline completion should remain terminal-safe",
            )

            completing_background = await runtime.task_runner.start_background(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.CHAT,
                    scope="runner:background-completion-cancel",
                    source=ExecutionTaskSource.SYSTEM,
                    label="runner-background-completion-cancel",
                    authority=SYSTEM_AUTHORITY,
                ),
                _complete_task,
            )
            await asyncio.wait_for(
                completion_entered["runner:background-completion-cancel"].wait(),
                timeout=1.0,
            )
            await runtime.task_coordinator.cancel_task(
                completing_background.task_id,
                reason="validation_completion_cancel",
            )
            background_completion_terminal = await self._wait_for_task_terminal(
                completing_background.task_id
            )
            self.soft_assert_equal(
                (
                    background_completion_terminal.status
                    if background_completion_terminal
                    else None
                ),
                "cancelled",
                "Cancellation during attached completion should remain terminal-safe",
            )
        finally:
            runtime.task_coordinator.mark_completed = original_mark_completed
            hold_completion.set()

        failure_entered: dict[str, asyncio.Event] = {
            "runner:inline-failure-cancel": asyncio.Event(),
            "runner:background-failure-cancel": asyncio.Event(),
        }
        release_failure: dict[str, asyncio.Event] = {
            scope: asyncio.Event() for scope in failure_entered
        }
        original_mark_failed = runtime.task_coordinator.mark_failed

        async def _delay_task_failure(task_id, *, reason=None, error_type=None):
            snapshot = await runtime.task_coordinator.get_task(task_id)
            scope = snapshot.scope if snapshot else ""
            if scope in failure_entered:
                failure_entered[scope].set()
                await release_failure[scope].wait()
            await original_mark_failed(
                task_id,
                reason=reason,
                error_type=error_type,
            )

        async def _raise_task_failure(_task):
            raise RuntimeError("forced terminalization race")

        runtime.task_coordinator.mark_failed = _delay_task_failure
        try:
            failing_inline = asyncio.create_task(
                runtime.task_runner.run_inline(
                    ExecutionTaskSpec(
                        kind=ExecutionTaskKind.CHAT,
                        scope="runner:inline-failure-cancel",
                        source=ExecutionTaskSource.SYSTEM,
                        label="runner-inline-failure-cancel",
                        authority=SYSTEM_AUTHORITY,
                    ),
                    _raise_task_failure,
                )
            )
            await asyncio.wait_for(
                failure_entered["runner:inline-failure-cancel"].wait(),
                timeout=1.0,
            )
            failing_inline.cancel()
            release_failure["runner:inline-failure-cancel"].set()
            try:
                await failing_inline
            except RuntimeError as exc:
                self.soft_assert_equal(
                    str(exc),
                    "forced terminalization race",
                    "Observed inline failure should win over later cancellation",
                )
            else:
                self.soft_assert(False, "Inline terminalization race should fail")
            inline_failure_records = await runtime.task_coordinator.list_tasks(
                scope="runner:inline-failure-cancel"
            )
            self.soft_assert_equal(
                [task.status for task in inline_failure_records],
                ["failed"],
                "Cancellation during inline failure publication should remain terminal-safe",
            )

            race_failure_hooks: list[tuple[str, str]] = []
            race_cancel_hooks: list[str] = []
            failing_background = await runtime.task_runner.start_background(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.CHAT,
                    scope="runner:background-failure-cancel",
                    source=ExecutionTaskSource.SYSTEM,
                    label="runner-background-failure-cancel",
                    authority=SYSTEM_AUTHORITY,
                ),
                _raise_task_failure,
                hooks=ExecutionTaskHooks(
                    on_failed=lambda task_id, exc: _record_failure(
                        race_failure_hooks, task_id, exc
                    ),
                    on_cancelled=lambda task_id: _record_task_id(
                        race_cancel_hooks, task_id
                    ),
                ),
            )
            await asyncio.wait_for(
                failure_entered["runner:background-failure-cancel"].wait(),
                timeout=1.0,
            )
            await runtime.task_coordinator.cancel_task(
                failing_background.task_id,
                reason="validation_failure_cancel",
            )
            release_failure["runner:background-failure-cancel"].set()
            background_failure_terminal = await self._wait_for_task_terminal(
                failing_background.task_id
            )
            self.soft_assert_equal(
                (
                    background_failure_terminal.status
                    if background_failure_terminal
                    else None
                ),
                "failed",
                "Observed background failure should win over later cancellation",
            )
            self.soft_assert_equal(
                race_failure_hooks,
                [(failing_background.task_id, "RuntimeError")],
                "Failure-winning races should call the failure hook exactly once",
            )
            self.soft_assert_equal(
                race_cancel_hooks,
                [],
                "Failure-winning races should not call the cancellation hook",
            )
        finally:
            runtime.task_coordinator.mark_failed = original_mark_failed
            for release in release_failure.values():
                release.set()

        warning_checkpoint = self.event_checkpoint()
        timed_out_tasks = []
        for suffix in ("one", "two"):
            timed_out_task = await runtime.task_coordinator.create_queued_task(
                kind=ExecutionTaskKind.DELEGATE,
                scope=f"runner:timeout-warning:{suffix}",
                source=ExecutionTaskSource.SYSTEM,
                label=f"runner-timeout-warning-{suffix}",
                authority=SYSTEM_AUTHORITY,
            )
            timed_out_tasks.append(timed_out_task)
            await runtime.task_coordinator.mark_timed_out(
                timed_out_task.task_id, reason="validation_timeout"
            )
        timeout_events = [
            event
            for event in self.events_since(warning_checkpoint)
            if event.get("name") == "execution_task_timed_out"
        ]
        self.soft_assert_equal(
            len(timeout_events),
            2,
            "Distinct task timeout warnings must survive warning deduplication",
        )

        failure_checkpoint = self.event_checkpoint()
        bounded_failure = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.DELEGATE,
            scope="runner:bounded-failure",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-bounded-failure",
            authority=SYSTEM_AUTHORITY,
        )
        await runtime.task_coordinator.mark_failed(
            bounded_failure.task_id,
            reason="sensitive-sentinel-" + ("x" * 1_000),
            error_type="ValidationFailure",
        )
        failure_events = [
            event
            for event in self.events_since(failure_checkpoint)
            if event.get("name") == "execution_task_failed"
        ]
        failure_data = failure_events[-1].get("data", {}) if failure_events else {}
        self.soft_assert_equal(
            failure_data.get("error_type"),
            "ValidationFailure",
            "Execution task failures should retain a structured error type",
        )
        self.soft_assert(
            len(str(failure_data.get("error") or "")) <= 503,
            "Execution task activity errors should remain bounded",
        )
        activity_response = self.call_api(
            "/api/system/activity-log?limit=100&tag=execution-tasks"
            f"&search={bounded_failure.task_id}"
        )
        self.soft_assert_equal(
            activity_response.status_code,
            200,
            "Execution task System Activity should be queryable",
        )
        activity_failures = [
            entry.get("data") or {}
            for entry in activity_response.json().get("entries", [])
            if (entry.get("data") or {}).get("event") == "execution_task_failed"
        ]
        self.soft_assert_equal(
            activity_failures[-1].get("error_type") if activity_failures else None,
            "ValidationFailure",
            "System Activity should retain the structured execution failure type",
        )
        self.soft_assert(
            bool(activity_failures)
            and len(str(activity_failures[-1].get("error") or "")) <= 503,
            "System Activity should retain only bounded execution failure text",
        )

        reservation_inserted = asyncio.Event()
        release_reservation = asyncio.Event()
        reservation_cancel_hooks: list[str] = []
        original_create_queued_task = runtime.task_coordinator.create_queued_task

        async def _delay_background_reservation(**kwargs):
            reserved = await original_create_queued_task(**kwargs)
            if kwargs.get("scope") == "runner:interrupted-reservation":
                reservation_inserted.set()
                await release_reservation.wait()
            return reserved

        async def _record_and_fail_reservation_hook(task_id):
            reservation_cancel_hooks.append(task_id)
            raise RuntimeError("forced reservation cancellation hook failure")

        runtime.task_coordinator.create_queued_task = _delay_background_reservation
        try:
            interrupted_start = asyncio.create_task(
                runtime.task_runner.start_background(
                    ExecutionTaskSpec(
                        kind=ExecutionTaskKind.DELEGATE,
                        scope="runner:interrupted-reservation",
                        source=ExecutionTaskSource.TOOL,
                        label="runner-interrupted-reservation",
                        authority=SYSTEM_AUTHORITY,
                        detached_from_parent_lifecycle=True,
                    ),
                    _complete_task,
                    hooks=ExecutionTaskHooks(
                        on_cancelled=_record_and_fail_reservation_hook
                    ),
                )
            )
            await asyncio.wait_for(reservation_inserted.wait(), timeout=1.0)
            interrupted_start.cancel()
            release_reservation.set()
            try:
                await interrupted_start
            except asyncio.CancelledError:
                pass
            else:
                self.soft_assert(False, "Interrupted background start should cancel")
        finally:
            runtime.task_coordinator.create_queued_task = original_create_queued_task
            release_reservation.set()
        interrupted_records = await runtime.task_coordinator.list_tasks(
            scope="runner:interrupted-reservation"
        )
        self.soft_assert_equal(
            [task.status for task in interrupted_records],
            ["cancelled"],
            "Interrupted background reservation should not strand a queued task",
        )
        self.soft_assert_equal(
            reservation_cancel_hooks,
            [interrupted_records[0].task_id] if interrupted_records else [],
            "Interrupted background reservation should run cancellation hooks",
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
        self.soft_assert_equal(
            terminal_only_wait.done(),
            False,
            "Terminal-only waits should ignore nonterminal attention state",
        )
        await runtime.task_coordinator.mark_completed(observable_task.task_id)
        terminal_only_result = await terminal_only_wait
        self.soft_assert_equal(
            terminal_only_result.snapshots[0].status,
            "completed",
            "Terminal-only waits should wake on terminal state",
        )

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
        self.soft_assert_equal(
            evicted_wait_result.timed_out,
            False,
            "Waiters should wake when a requested terminal task is evicted",
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
                "artifact_references": [
                    "vault://report.md",
                    "vault://" + ("oversized-reference" * 10_000),
                ],
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
        self.soft_assert(
            len(
                json.dumps(
                    result_snapshot.result,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            <= EXECUTION_TASK_RESULT_MAX_CHARS,
            "Execution task result bounds should include artifact references",
        )

        parent = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:parent",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-parent",
            authority=SYSTEM_AUTHORITY,
        )
        first_child = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:child",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-first-child",
            authority=SYSTEM_AUTHORITY,
            parent_task_id=parent.task_id,
        )
        second_child = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope="runner:child",
            source=ExecutionTaskSource.SYSTEM,
            label="runner-second-child",
            authority=SYSTEM_AUTHORITY,
            parent_task_id=parent.task_id,
        )
        detached_log_checkpoint = self.event_checkpoint()
        completion_survivor = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.DELEGATE,
            scope="runner:child",
            source=ExecutionTaskSource.TOOL,
            label="runner-completion-survivor",
            authority=SYSTEM_AUTHORITY,
            parent_task_id=parent.task_id,
            detached_from_parent_lifecycle=True,
        )
        self.assert_event_contains(
            self.events_since(detached_log_checkpoint),
            name="execution_task_created",
            expected={
                "event": "execution_task_created",
                "status": "queued",
                "task_id": completion_survivor.task_id,
                "parent_task_id": parent.task_id,
                "detached_from_parent_lifecycle": True,
            },
        )
        try:
            await runtime.task_coordinator.create_queued_task(
                kind=ExecutionTaskKind.DELEGATE,
                scope="runner:missing-parent-child",
                source=ExecutionTaskSource.TOOL,
                label="runner-missing-parent-child",
                authority=SYSTEM_AUTHORITY,
                parent_task_id="task_missing_parent",
                detached_from_parent_lifecycle=True,
            )
        except RuntimeError as exc:
            self.soft_assert(
                "Parent execution task not found" in str(exc),
                "Detached child lineage should require an existing parent",
            )
        else:
            self.soft_assert(False, "Detached child must reject missing lineage")
        try:
            await runtime.task_coordinator.create_queued_task(
                kind=ExecutionTaskKind.DELEGATE,
                scope="runner:cross-authority-child",
                source=ExecutionTaskSource.TOOL,
                label="runner-cross-authority-child",
                authority=ExecutionAuthority(principal_id="different-principal"),
                parent_task_id=parent.task_id,
                detached_from_parent_lifecycle=True,
            )
        except RuntimeError as exc:
            self.soft_assert(
                "authority must match" in str(exc),
                "Detached child lineage should preserve parent authority",
            )
        else:
            self.soft_assert(
                False, "Detached child must reject cross-authority lineage"
            )
        await runtime.task_coordinator.cancel_task(
            first_child.task_id,
            reason="validation_direct_child_cancel",
        )
        unaffected_parent = await runtime.task_coordinator.get_task(parent.task_id)
        unaffected_sibling = await runtime.task_coordinator.get_task(
            second_child.task_id
        )
        self.soft_assert_equal(
            unaffected_parent.status if unaffected_parent else None,
            "queued",
            "Direct child cancellation should not cancel its parent",
        )
        self.soft_assert_equal(
            unaffected_sibling.status if unaffected_sibling else None,
            "queued",
            "Direct child cancellation should not cancel siblings",
        )
        child_cancellation_started = asyncio.Event()
        release_child_cancellation = asyncio.Event()
        original_cancel_task = runtime.task_coordinator.cancel_task

        async def _delay_child_cancellation(task_id, *, reason="cancel_requested"):
            if task_id == second_child.task_id:
                child_cancellation_started.set()
                await release_child_cancellation.wait()
            return await original_cancel_task(task_id, reason=reason)

        runtime.task_coordinator.cancel_task = _delay_child_cancellation
        parent_completion = asyncio.create_task(
            runtime.task_coordinator.mark_completed(parent.task_id)
        )
        try:
            await asyncio.wait_for(child_cancellation_started.wait(), timeout=1.0)
            try:
                await runtime.task_coordinator.create_queued_task(
                    kind=ExecutionTaskKind.CHAT,
                    scope="runner:late-child",
                    source=ExecutionTaskSource.SYSTEM,
                    label="runner-late-attached-child",
                    authority=SYSTEM_AUTHORITY,
                    parent_task_id=parent.task_id,
                )
            except RuntimeError as exc:
                self.soft_assert(
                    "no longer accepting children" in str(exc),
                    "Parent transition should close attached-child admission",
                )
            else:
                self.soft_assert(
                    False,
                    "An attached child must not enter after parent transition starts",
                )
        finally:
            release_child_cancellation.set()
            await parent_completion
            runtime.task_coordinator.cancel_task = original_cancel_task
        cascaded_child = await runtime.task_coordinator.get_task(second_child.task_id)
        surviving_child = await runtime.task_coordinator.get_task(
            completion_survivor.task_id
        )
        self.soft_assert_equal(
            cascaded_child.status if cascaded_child else None,
            "cancelled",
            "A parent transition should cancel lifecycle-owned child tasks",
        )
        self.soft_assert_equal(
            surviving_child.status if surviving_child else None,
            "queued",
            "Normal parent completion should preserve an opted-in child task",
        )
        self.soft_assert_equal(
            (
                surviving_child.detached_from_parent_lifecycle
                if surviving_child
                else None
            ),
            True,
            "The child snapshot should expose its detached lifecycle policy",
        )
        try:
            await runtime.task_coordinator.create_queued_task(
                kind=ExecutionTaskKind.DELEGATE,
                scope="runner:terminal-parent-child",
                source=ExecutionTaskSource.TOOL,
                label="runner-terminal-parent-child",
                authority=SYSTEM_AUTHORITY,
                parent_task_id=parent.task_id,
                detached_from_parent_lifecycle=True,
            )
        except RuntimeError as exc:
            self.soft_assert(
                "no longer accepting children" in str(exc),
                "Detached child lineage should require an active parent at launch",
            )
        else:
            self.soft_assert(False, "Detached child must reject terminal lineage")
        await runtime.task_coordinator.cancel_task(
            completion_survivor.task_id,
            reason="validation_completion_survivor_cleanup",
        )

        parent_transitions = {
            "failed": runtime.task_coordinator.mark_failed,
            "cancelled": runtime.task_coordinator.mark_cancelled,
            "timed_out": runtime.task_coordinator.mark_timed_out,
            "skipped": runtime.task_coordinator.mark_skipped,
        }
        for transition_name, transition in parent_transitions.items():
            abnormal_parent = await runtime.task_coordinator.create_queued_task(
                kind=ExecutionTaskKind.CHAT,
                scope=f"runner:{transition_name}-parent",
                source=ExecutionTaskSource.SYSTEM,
                label=f"runner-{transition_name}-parent",
                authority=SYSTEM_AUTHORITY,
            )
            detached_child = await runtime.task_coordinator.create_queued_task(
                kind=ExecutionTaskKind.DELEGATE,
                scope=f"runner:{transition_name}-parent-child",
                source=ExecutionTaskSource.TOOL,
                label=f"runner-{transition_name}-child",
                authority=SYSTEM_AUTHORITY,
                parent_task_id=abnormal_parent.task_id,
                detached_from_parent_lifecycle=True,
            )
            await transition(
                abnormal_parent.task_id,
                reason=f"validation_parent_{transition_name}",
            )
            surviving_abnormal_child = await runtime.task_coordinator.get_task(
                detached_child.task_id
            )
            self.soft_assert_equal(
                (surviving_abnormal_child.status if surviving_abnormal_child else None),
                "queued",
                f"A detached child should survive parent {transition_name}",
            )
            await runtime.task_coordinator.cancel_task(
                detached_child.task_id,
                reason=f"validation_{transition_name}_survivor_cleanup",
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
        natural_timeout = await self._wait_for_task_terminal(
            natural_timeout_task.task_id
        )
        self.soft_assert_equal(
            natural_timeout.status if natural_timeout else None,
            "failed",
            "An inner TimeoutError should remain a domain failure",
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
                        concurrency_state,
                        concurrency_entered,
                        concurrency_release,
                    ),
                ),
                start_immediately=False,
            )
            for index in range(3)
        ]
        await asyncio.wait_for(concurrency_entered.wait(), timeout=2.0)
        queued_concurrency = await self._wait_for_task_metadata(
            concurrency_tasks[2].task_id,
            "queue_reason",
            "validation_capacity",
        )
        self.soft_assert_equal(
            queued_concurrency.status if queued_concurrency else None,
            "queued",
            "Capacity-delayed tasks should remain queued until admitted",
        )
        self.soft_assert_equal(
            (
                queued_concurrency.metadata.get("queue_position")
                if queued_concurrency
                else None
            ),
            1,
            "Capacity-delayed tasks should expose their queue position",
        )
        concurrency_release.set()
        for task in concurrency_tasks:
            await self._wait_for_task_terminal(task.task_id)
        self.soft_assert_equal(
            concurrency_state["maximum"],
            2,
            "Runner concurrency lanes should enforce the configured limit",
        )
        self.soft_assert_equal(
            concurrency_state["entered"],
            3,
            "Queued capacity work should run after a slot is released",
        )

        single_entered = asyncio.Event()
        single_release = asyncio.Event()
        queued_callback_entered = asyncio.Event()
        third_entered = asyncio.Event()

        async def _block_second_queue_callback(
            task,
            _wait,
        ) -> None:
            if task.label == "runner-cancelled-waiter":
                queued_callback_entered.set()
                await asyncio.Event().wait()

        cancellation_policy = ExecutionConcurrencyPolicy(
            key="runner:queued-cancellation",
            limit=1,
            queued_status="queued_for_capacity",
            queued_metadata={"queue_reason": "validation_capacity"},
            on_queued=_block_second_queue_callback,
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
                task,
                cancellation_policy,
                lambda: _mark_capacity_entered(third_entered),
            ),
            start_immediately=False,
        )
        await asyncio.wait_for(queued_callback_entered.wait(), timeout=2.0)
        await runtime.task_coordinator.cancel_task(
            cancelled_waiter.task_id,
            reason="validation_cancel_queued_waiter",
        )
        await self._wait_for_task_terminal(cancelled_waiter.task_id)
        successor = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:queued-cancellation",
                source=ExecutionTaskSource.SYSTEM,
                label="runner-capacity-successor",
                authority=SYSTEM_AUTHORITY,
            ),
            lambda task: runtime.task_runner.run_with_concurrency(
                task,
                cancellation_policy,
                lambda: _mark_capacity_entered(third_entered),
            ),
            start_immediately=False,
        )
        single_release.set()
        await asyncio.wait_for(third_entered.wait(), timeout=2.0)
        await self._wait_for_task_terminal(first_capacity_task.task_id)
        successor_terminal = await self._wait_for_task_terminal(successor.task_id)
        self.soft_assert_equal(
            successor_terminal.status if successor_terminal else None,
            "completed",
            "Cancelling during queue publication should not leak capacity",
        )

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
                    task,
                    live_queue_policy,
                    lambda: asyncio.sleep(0),
                ),
                start_immediately=False,
            )

        live_waiter_one = await _start_live_waiter("live-queue-one")
        live_waiter_two = await _start_live_waiter("live-queue-two")
        await self._wait_for_task_metadata(live_waiter_two.task_id, "queue_position", 2)
        await runtime.task_coordinator.cancel_task(
            live_waiter_one.task_id,
            reason="validation_live_queue_cancel",
        )
        await self._wait_for_task_terminal(live_waiter_one.task_id)
        promoted_position = await self._wait_for_task_metadata(
            live_waiter_two.task_id,
            "queue_position",
            1,
        )
        self.soft_assert_equal(
            (
                promoted_position.metadata.get("queue_position")
                if promoted_position
                else None
            ),
            1,
            "Remaining capacity waiters should expose a live queue position",
        )
        live_queue_release.set()
        await self._wait_for_task_terminal(live_holder.task_id)
        await self._wait_for_task_terminal(live_waiter_two.task_id)

        cleanup_guard_release = asyncio.Event()
        cleanup_holder_entered = asyncio.Event()
        cleanup_policy = ExecutionConcurrencyPolicy(
            key="runner:cancelled-cleanup",
            limit=1,
            queued_status="queued_for_capacity",
        )
        cleanup_holder = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:cancelled-cleanup",
                source=ExecutionTaskSource.SYSTEM,
                label="cancelled-cleanup-holder",
                authority=SYSTEM_AUTHORITY,
            ),
            lambda task: runtime.task_runner.run_with_concurrency(
                task,
                cleanup_policy,
                lambda: _hold_capacity(
                    cleanup_holder_entered,
                    cleanup_guard_release,
                ),
            ),
            start_immediately=False,
        )
        await asyncio.wait_for(cleanup_holder_entered.wait(), timeout=2.0)
        await runtime.task_runner._gate_guard.acquire()  # noqa: SLF001
        cleanup_guard_release.set()
        await asyncio.sleep(0.02)
        await runtime.task_coordinator.cancel_task(
            cleanup_holder.task_id,
            reason="validation_cancel_during_cleanup",
        )
        runtime.task_runner._gate_guard.release()  # noqa: SLF001
        cleanup_successor_entered = asyncio.Event()
        cleanup_successor = await runtime.task_runner.start_background(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.CHAT,
                scope="runner:cancelled-cleanup",
                source=ExecutionTaskSource.SYSTEM,
                label="cancelled-cleanup-successor",
                authority=SYSTEM_AUTHORITY,
            ),
            lambda task: runtime.task_runner.run_with_concurrency(
                task,
                cleanup_policy,
                lambda: _mark_capacity_entered(cleanup_successor_entered),
            ),
            start_immediately=False,
        )
        await asyncio.wait_for(cleanup_successor_entered.wait(), timeout=2.0)
        await self._wait_for_task_terminal(cleanup_holder.task_id)
        cleanup_successor_terminal = await self._wait_for_task_terminal(
            cleanup_successor.task_id
        )
        self.soft_assert_equal(
            cleanup_successor_terminal.status if cleanup_successor_terminal else None,
            "completed",
            "Cancellation during cleanup should not leak concurrency capacity",
        )

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

        dynamic_tasks = [
            await _start_dynamic("dynamic-one", dynamic_policy_one),
        ]
        while dynamic_state["active"] < 1:
            await asyncio.sleep(0.01)
        dynamic_tasks.append(await _start_dynamic("dynamic-two", dynamic_policy_one))
        await self._wait_for_task_metadata(
            dynamic_tasks[1].task_id,
            "queue_reason",
            "dynamic_capacity",
        )
        dynamic_tasks.extend(
            [
                await _start_dynamic("dynamic-three", dynamic_policy_two),
                await _start_dynamic("dynamic-four", dynamic_policy_two),
            ]
        )
        await asyncio.wait_for(dynamic_two_active.wait(), timeout=2.0)
        await asyncio.sleep(0.02)
        self.soft_assert_equal(
            dynamic_three_active.is_set(),
            False,
            "Changing a concurrency limit should not create an independent lane",
        )
        dynamic_release.set()
        for task in dynamic_tasks:
            await self._wait_for_task_terminal(task.task_id)
        self.soft_assert_equal(
            dynamic_state["maximum"],
            2,
            "A live concurrency setting change should preserve one process-wide cap",
        )

        transition_release = asyncio.Event()
        transition_two_active = asyncio.Event()
        transition_state = {"active": 0, "maximum": 0}
        unlimited_policy = ExecutionConcurrencyPolicy(
            key="runner:unlimited-transition",
            limit=0,
            queued_status="queued_for_capacity",
        )
        limited_policy = ExecutionConcurrencyPolicy(
            key="runner:unlimited-transition",
            limit=1,
            queued_status="queued_for_capacity",
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
                task,
                limited_policy,
                lambda: _mark_capacity_entered(limited_entered),
            ),
            start_immediately=False,
        )
        await asyncio.sleep(0.02)
        self.soft_assert_equal(
            limited_entered.is_set(),
            False,
            "A 0-to-1 limit change should account for already-active holders",
        )
        transition_release.set()
        await asyncio.wait_for(limited_entered.wait(), timeout=2.0)
        for task in [*transition_tasks, limited_task]:
            await self._wait_for_task_terminal(task.task_id)

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
        detached_shutdown_child = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.DELEGATE,
            scope="runner:shutdown-child",
            source=ExecutionTaskSource.TOOL,
            label="runner-detached-shutdown-child",
            authority=SYSTEM_AUTHORITY,
            parent_task_id=shutdown_task.task_id,
            detached_from_parent_lifecycle=True,
        )
        await self._wait_for_task_status(shutdown_task.task_id, "running")
        await runtime.task_coordinator.shutdown(reason="validation_shutdown")
        await asyncio.wait_for(cleanup_started.wait(), timeout=2.0)
        try:
            await runtime.task_coordinator.create_queued_task(
                kind=ExecutionTaskKind.DELEGATE,
                scope="runner:late-shutdown-child",
                source=ExecutionTaskSource.TOOL,
                label="runner-late-shutdown-child",
                authority=SYSTEM_AUTHORITY,
                detached_from_parent_lifecycle=True,
            )
        except RuntimeError as exc:
            self.soft_assert_equal(
                str(exc),
                "Execution task coordinator is shutting down",
                "Shutdown admission should fail with a stable explanation",
            )
        else:
            self.soft_assert(False, "Runtime shutdown must close task admission")
        shutdown_child = await runtime.task_coordinator.get_task(
            detached_shutdown_child.task_id
        )
        self.soft_assert_equal(
            shutdown_child.status if shutdown_child else None,
            "cancelled",
            "Runtime shutdown should cancel detached child tasks",
        )
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


async def _raise_timeout_error(_task) -> None:
    raise TimeoutError("domain timeout")


async def _record_task_id(task_ids: list[str], task_id: str) -> None:
    task_ids.append(task_id)


async def _record_failure(
    failures: list[tuple[str, str]],
    task_id: str,
    exc: BaseException,
) -> None:
    failures.append((task_id, type(exc).__name__))


async def _raise_cancel_hook(_task_id: str) -> None:
    raise RuntimeError("forced cancellation hook failure")


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


async def _hold_concurrency_slot(
    state: dict[str, int],
    entered: asyncio.Event,
    release: asyncio.Event,
) -> None:
    state["active"] += 1
    state["entered"] += 1
    state["maximum"] = max(state["maximum"], state["active"])
    if state["active"] == 2:
        entered.set()
    try:
        await release.wait()
    finally:
        state["active"] -= 1


async def _hold_capacity(entered: asyncio.Event, release: asyncio.Event) -> None:
    entered.set()
    await release.wait()


async def _mark_capacity_entered(entered: asyncio.Event) -> None:
    entered.set()


async def _hold_dynamic_capacity(
    state: dict[str, int],
    two_active: asyncio.Event,
    three_active: asyncio.Event,
    release: asyncio.Event,
) -> None:
    state["active"] += 1
    state["maximum"] = max(state["maximum"], state["active"])
    if state["active"] >= 2:
        two_active.set()
    if state["active"] >= 3:
        three_active.set()
    try:
        await release.wait()
    finally:
        state["active"] -= 1
