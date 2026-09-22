"""Generic runtime execution task runner shell."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from core.identity import ExecutionAuthority
from core.logger import UnifiedLogger
from core.runtime.background import RuntimeBackgroundSpawner
from core.runtime.execution_tasks import (
    ExecutionTaskSnapshot,
    ExecutionTaskStatus,
    TaskCoordinator,
)

logger = UnifiedLogger(tag="execution-task-runner")


@dataclass(frozen=True)
class ExecutionTaskSpec:
    """Identity and metadata for one runtime execution task."""

    kind: str
    scope: str
    source: str
    label: str
    authority: ExecutionAuthority
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_task_id: str | None = None
    detached_from_parent_lifecycle: bool = False
    timeout_seconds: float | None = None
    timeout_reason: str | None = None


@dataclass(frozen=True)
class ExecutionTaskRunOutcome:
    """Domain result plus the execution-task terminal state it represents."""

    value: Any
    status: ExecutionTaskStatus = ExecutionTaskStatus.COMPLETED
    reason: str | None = None


@dataclass(frozen=True)
class ExecutionTaskHooks:
    """Optional domain hooks around generic execution task lifecycle."""

    on_cancelled: Callable[[str], Awaitable[None]] | None = None
    on_failed: Callable[[str, BaseException], Awaitable[None]] | None = None
    on_timed_out: Callable[[str, float, str], Awaitable[Any]] | None = None


@dataclass(frozen=True)
class ExecutionGateWait:
    """Observable queue position for a task waiting on an execution gate."""

    key: str
    queue_position: int
    holder_task_id: str | None


@dataclass(frozen=True)
class ExecutionGatePolicy:
    """Keyed lane policy for serializing related execution tasks."""

    key: str
    queued_status: str
    queued_metadata: dict[str, Any] = field(default_factory=dict)
    queue_position_key: str | None = "queue_position"
    holder_task_id_key: str | None = "waiting_for_task_id"
    clear_metadata: dict[str, Any] = field(default_factory=dict)
    on_queued: (
        Callable[[ExecutionTaskSnapshot, ExecutionGateWait], Awaitable[None]] | None
    ) = None


@dataclass(frozen=True)
class ExecutionConcurrencyWait:
    """Observable position for work waiting on bounded concurrency."""

    key: str
    queue_position: int
    active_task_ids: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionConcurrencyPolicy:
    """Policy for one shared bounded-concurrency execution lane."""

    key: str
    limit: int
    queued_status: str
    queued_metadata: dict[str, Any] = field(default_factory=dict)
    clear_metadata: dict[str, Any] = field(default_factory=dict)
    on_queued: (
        Callable[[ExecutionTaskSnapshot, ExecutionConcurrencyWait], Awaitable[None]]
        | None
    ) = None


@dataclass
class _ExecutionConcurrencyLane:
    limit: int
    holders: set[str] = field(default_factory=set)
    waiters: list[_ExecutionConcurrencyWaiter] = field(default_factory=list)


@dataclass(frozen=True)
class _ExecutionConcurrencyWaiter:
    task_id: str
    future: asyncio.Future[None]
    queued_status: str
    queued_metadata: dict[str, Any]


class ExecutionTaskRunner:
    """Create, attach, and run background execution tasks."""

    def __init__(
        self,
        *,
        task_coordinator: TaskCoordinator,
        background_spawner: RuntimeBackgroundSpawner,
    ) -> None:
        self._task_coordinator = task_coordinator
        self._background_spawner = background_spawner
        self._gate_guard = asyncio.Lock()
        self._gate_locks: dict[str, asyncio.Lock] = {}
        self._gate_holders: dict[str, str] = {}
        self._gate_waiters: dict[str, list[str]] = {}
        self._concurrency_lanes: dict[str, _ExecutionConcurrencyLane] = {}

    async def start_background(
        self,
        spec: ExecutionTaskSpec,
        run: Callable[[ExecutionTaskSnapshot], Awaitable[Any]],
        *,
        hooks: ExecutionTaskHooks | None = None,
        start_immediately: bool = True,
    ) -> ExecutionTaskSnapshot:
        """Create a queued task and run it in the runtime background."""
        reservation = asyncio.create_task(
            self._task_coordinator.create_queued_task(
                kind=spec.kind,
                scope=spec.scope,
                source=spec.source,
                label=spec.label,
                authority=spec.authority,
                metadata=spec.metadata,
                parent_task_id=spec.parent_task_id,
                detached_from_parent_lifecycle=spec.detached_from_parent_lifecycle,
                awaiting_handle=True,
            )
        )
        try:
            task = await asyncio.shield(reservation)
        except asyncio.CancelledError:
            await self._abort_background_reservation(reservation, hooks)
            raise

        async def _run() -> None:
            try:
                async with self._task_coordinator.track_existing_task(
                    task.task_id
                ) as tracked_task:
                    if start_immediately:
                        await self._task_coordinator.mark_started(tracked_task.task_id)
                    result = await self.run_with_timeout(
                        tracked_task,
                        spec,
                        lambda: run(tracked_task),
                        hooks=hooks,
                    )
                    await self._publish_run_outcome(tracked_task.task_id, result)
            except asyncio.CancelledError:
                await self._finish_cancelled_task(hooks, task.task_id)
                raise
            except (
                Exception
            ) as exc:  # noqa: BLE001 - task status is recorded by coordinator
                if await self._task_has_cancelled(task.task_id):
                    await self._finish_cancelled_task(hooks, task.task_id)
                    return
                await self._call_failed_hook(hooks, task.task_id, exc)
                return

        self._background_spawner.spawn(_run)
        return task

    async def run_inline(
        self,
        spec: ExecutionTaskSpec,
        run: Callable[[ExecutionTaskSnapshot], Awaitable[Any]],
        *,
        hooks: ExecutionTaskHooks | None = None,
        start_immediately: bool = True,
    ) -> Any:
        """Run work in the current coroutine under execution task ownership."""
        if spec.detached_from_parent_lifecycle:
            raise ValueError("Inline execution tasks cannot detach from their caller")
        async with self._task_coordinator.track_current_task(
            kind=spec.kind,
            scope=spec.scope,
            source=spec.source,
            label=spec.label,
            authority=spec.authority,
            metadata=spec.metadata,
            parent_task_id=spec.parent_task_id,
            detached_from_parent_lifecycle=spec.detached_from_parent_lifecycle,
            start_immediately=start_immediately,
        ) as task:
            result = await self.run_with_timeout(
                task,
                spec,
                lambda: run(task),
                hooks=hooks,
            )
            return await self._publish_run_outcome(task.task_id, result)

    async def _abort_background_reservation(
        self,
        reservation: asyncio.Task[ExecutionTaskSnapshot],
        hooks: ExecutionTaskHooks | None,
    ) -> None:
        async def _cleanup() -> None:
            try:
                task = await reservation
            except Exception:
                return
            try:
                await self._call_cancelled_hook(hooks, task.task_id)
            finally:
                await self._task_coordinator.mark_cancelled(
                    task.task_id,
                    reason="background_start_cancelled",
                )

        cleanup = asyncio.create_task(_cleanup())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                continue
        await cleanup

    async def run_with_timeout(
        self,
        task: ExecutionTaskSnapshot,
        spec: ExecutionTaskSpec,
        run: Callable[[], Awaitable[Any]],
        *,
        hooks: ExecutionTaskHooks | None = None,
    ) -> Any:
        """Run work under an optional execution-task timeout."""
        timeout = spec.timeout_seconds
        if timeout is None or timeout <= 0:
            return await run()

        deadline = asyncio.timeout(timeout)
        try:
            async with deadline:
                return await run()
        except TimeoutError:
            if not deadline.expired():
                raise
            reason = spec.timeout_reason or f"execution_task_timeout:{timeout:g}s"
            result = await self._call_timed_out_hook(
                hooks, task.task_id, timeout, reason
            )
            if isinstance(result, dict):
                await self._task_coordinator.record_result(task.task_id, result)
            await self._task_coordinator.mark_timed_out(task.task_id, reason=reason)
            return result

    async def _publish_run_outcome(self, task_id: str, result: Any) -> Any:
        outcome = (
            result
            if isinstance(result, ExecutionTaskRunOutcome)
            else ExecutionTaskRunOutcome(value=result)
        )
        if isinstance(outcome.value, dict):
            await self._task_coordinator.record_result(task_id, outcome.value)
        if outcome.status == ExecutionTaskStatus.FAILED:
            await self._task_coordinator.mark_failed(task_id, reason=outcome.reason)
        elif outcome.status == ExecutionTaskStatus.CANCELLED:
            await self._task_coordinator.mark_cancelled(task_id, reason=outcome.reason)
        elif outcome.status == ExecutionTaskStatus.TIMED_OUT:
            await self._task_coordinator.mark_timed_out(task_id, reason=outcome.reason)
        elif outcome.status == ExecutionTaskStatus.SKIPPED:
            await self._task_coordinator.mark_skipped(task_id, reason=outcome.reason)
        return outcome.value

    async def run_with_gate(
        self,
        task: ExecutionTaskSnapshot,
        policy: ExecutionGatePolicy,
        run: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run work after acquiring a keyed execution gate."""
        lock = await self._get_gate_lock(policy.key)
        wait = await self._register_gate_waiter(task.task_id, policy.key, lock)
        if wait is not None:
            metadata = dict(policy.queued_metadata)
            if policy.queue_position_key is not None:
                metadata[policy.queue_position_key] = wait.queue_position
            if policy.holder_task_id_key is not None:
                metadata[policy.holder_task_id_key] = wait.holder_task_id
            await self._task_coordinator.heartbeat(
                task.task_id,
                status=policy.queued_status,
                metadata=metadata,
            )
            if policy.on_queued is not None:
                await policy.on_queued(task, wait)

        acquired = False
        try:
            await lock.acquire()
            acquired = True
            await self._mark_gate_acquired(task.task_id, policy.key)
            if policy.clear_metadata:
                await self._task_coordinator.update_metadata(
                    task.task_id, policy.clear_metadata
                )
            return await run()
        finally:
            await self._unregister_gate_waiter(task.task_id, policy.key)
            if acquired:
                await self._release_gate(task.task_id, policy.key, lock)

    async def run_with_concurrency(
        self,
        task: ExecutionTaskSnapshot,
        policy: ExecutionConcurrencyPolicy,
        run: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Run work in one deterministic bounded-concurrency lane."""
        wait: ExecutionConcurrencyWait | None = None
        waiter: asyncio.Future[None] | None = None
        try:
            wait, waiter = await self._reserve_concurrency_slot(task.task_id, policy)
            if wait is not None:
                if policy.on_queued is not None:
                    await policy.on_queued(task, wait)
            if waiter is not None:
                await waiter
            await self._task_coordinator.mark_started(task.task_id)
            if policy.clear_metadata:
                await self._task_coordinator.update_metadata(
                    task.task_id,
                    policy.clear_metadata,
                )
            return await run()
        finally:
            await self._finish_concurrency_cleanup(task.task_id, policy.key)

    async def _task_has_cancelled(self, task_id: str) -> bool:
        snapshot = await self._task_coordinator.get_task(task_id)
        if snapshot is None:
            return True
        return snapshot.status == "cancelled" or snapshot.cancel_requested

    async def _finish_cancelled_task(
        self,
        hooks: ExecutionTaskHooks | None,
        task_id: str,
    ) -> None:
        async def _cleanup() -> None:
            try:
                await self._call_cancelled_hook(hooks, task_id)
            finally:
                await self._task_coordinator.mark_cancelled(task_id, reason="cancelled")

        cleanup = asyncio.create_task(_cleanup())
        cancelled_during_cleanup = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled_during_cleanup = True
        await cleanup
        if cancelled_during_cleanup:
            raise asyncio.CancelledError

    async def _get_gate_lock(self, key: str) -> asyncio.Lock:
        async with self._gate_guard:
            lock = self._gate_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._gate_locks[key] = lock
            return lock

    async def _reserve_concurrency_slot(
        self,
        task_id: str,
        policy: ExecutionConcurrencyPolicy,
    ) -> tuple[ExecutionConcurrencyWait | None, asyncio.Future[None] | None]:
        async with self._gate_guard:
            lane = self._concurrency_lanes.get(policy.key)
            if lane is None:
                lane = _ExecutionConcurrencyLane(limit=policy.limit)
                self._concurrency_lanes[policy.key] = lane
            else:
                lane.limit = policy.limit
                self._admit_concurrency_waiters(lane)
            if self._concurrency_has_capacity(lane) and not lane.waiters:
                lane.holders.add(task_id)
                await self._publish_concurrency_waiters(lane)
                return None, None
            waiter = asyncio.get_running_loop().create_future()
            lane.waiters.append(
                _ExecutionConcurrencyWaiter(
                    task_id=task_id,
                    future=waiter,
                    queued_status=policy.queued_status,
                    queued_metadata=dict(policy.queued_metadata),
                )
            )
            await self._publish_concurrency_waiters(lane)
            return (
                ExecutionConcurrencyWait(
                    key=policy.key,
                    queue_position=len(lane.waiters),
                    active_task_ids=tuple(sorted(lane.holders)),
                ),
                waiter,
            )

    async def _cleanup_concurrency_registration(
        self,
        task_id: str,
        key: str,
    ) -> None:
        async with self._gate_guard:
            lane = self._concurrency_lanes.get(key)
            if lane is None:
                return
            lane.waiters = [item for item in lane.waiters if item.task_id != task_id]
            lane.holders.discard(task_id)
            self._admit_concurrency_waiters(lane)
            await self._publish_concurrency_waiters(lane)
            if not lane.holders and not lane.waiters:
                self._concurrency_lanes.pop(key, None)

    async def _finish_concurrency_cleanup(self, task_id: str, key: str) -> None:
        cleanup = asyncio.create_task(
            self._cleanup_concurrency_registration(task_id, key)
        )
        cancelled_during_cleanup = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled_during_cleanup = True
        await cleanup
        if cancelled_during_cleanup:
            raise asyncio.CancelledError

    @staticmethod
    def _admit_concurrency_waiters(lane: _ExecutionConcurrencyLane) -> None:
        while lane.waiters and ExecutionTaskRunner._concurrency_has_capacity(lane):
            queued = lane.waiters.pop(0)
            if queued.future.cancelled():
                continue
            lane.holders.add(queued.task_id)
            queued.future.set_result(None)

    @staticmethod
    def _concurrency_has_capacity(lane: _ExecutionConcurrencyLane) -> bool:
        return lane.limit <= 0 or len(lane.holders) < lane.limit

    async def _publish_concurrency_waiters(
        self,
        lane: _ExecutionConcurrencyLane,
    ) -> None:
        active_task_ids = sorted(lane.holders)
        for position, queued in enumerate(lane.waiters, start=1):
            await self._task_coordinator.heartbeat(
                queued.task_id,
                status=queued.queued_status,
                metadata={
                    **queued.queued_metadata,
                    "queue_position": position,
                    "active_task_ids": active_task_ids,
                },
            )

    async def _register_gate_waiter(
        self,
        task_id: str,
        key: str,
        lock: asyncio.Lock,
    ) -> ExecutionGateWait | None:
        async with self._gate_guard:
            if not lock.locked():
                return None
            waiters = self._gate_waiters.setdefault(key, [])
            waiters.append(task_id)
            return ExecutionGateWait(
                key=key,
                queue_position=len(waiters),
                holder_task_id=self._gate_holders.get(key),
            )

    async def _unregister_gate_waiter(self, task_id: str, key: str) -> None:
        async with self._gate_guard:
            waiters = self._gate_waiters.get(key)
            if waiters is None:
                return
            if task_id in waiters:
                waiters.remove(task_id)
            if not waiters:
                self._gate_waiters.pop(key, None)

    async def _mark_gate_acquired(self, task_id: str, key: str) -> None:
        async with self._gate_guard:
            self._gate_holders[key] = task_id

    async def _release_gate(self, task_id: str, key: str, lock: asyncio.Lock) -> None:
        async with self._gate_guard:
            if self._gate_holders.get(key) == task_id:
                self._gate_holders.pop(key, None)
        lock.release()

    @staticmethod
    async def _call_cancelled_hook(
        hooks: ExecutionTaskHooks | None, task_id: str
    ) -> None:
        if hooks is not None and hooks.on_cancelled is not None:
            try:
                await hooks.on_cancelled(task_id)
            except Exception as exc:  # noqa: BLE001 - lifecycle cleanup must continue
                logger.error(
                    "execution_task_cancel_hook_failed",
                    data={
                        "event": "execution_task_cancel_hook_failed",
                        "status": "failed",
                        "task_id": task_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

    @staticmethod
    async def _call_failed_hook(
        hooks: ExecutionTaskHooks | None,
        task_id: str,
        exc: BaseException,
    ) -> None:
        if hooks is not None and hooks.on_failed is not None:
            await hooks.on_failed(task_id, exc)

    @staticmethod
    async def _call_timed_out_hook(
        hooks: ExecutionTaskHooks | None,
        task_id: str,
        timeout_seconds: float,
        reason: str,
    ) -> Any:
        if hooks is not None and hooks.on_timed_out is not None:
            return await hooks.on_timed_out(task_id, timeout_seconds, reason)
        return None
