"""Real task-runner workload callbacks and bounded public snapshot observations."""

from __future__ import annotations

import asyncio


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


async def wait_terminal(runtime, task_id):
    return await wait_snapshot(
        runtime.task_coordinator, task_id, lambda task: task.is_terminal
    )


async def wait_metadata(runtime, task_id, key, value):
    return await wait_snapshot(
        runtime.task_coordinator, task_id, lambda task: task.metadata.get(key) == value
    )


async def wait_status(runtime, task_id, status):
    return await wait_snapshot(
        runtime.task_coordinator, task_id, lambda task: task.status == status
    )


async def wait_snapshot(coordinator, task_id, predicate):
    async with asyncio.timeout(5):
        while True:
            task = await coordinator.get_task(task_id)
            if task is not None and predicate(task):
                return task
            await asyncio.sleep(0.01)


def block_cancelled_waiter(entered):
    async def on_queued(task, _wait):
        if task.label == "runner-cancelled-waiter":
            entered.set()
            await asyncio.Event().wait()

    return on_queued


async def oversized_result(_task):
    return {
        "text": "x" * 70_000,
        "artifact_references": [
            "vault://report.md",
            "vault://" + "oversized-reference" * 10_000,
        ],
    }


class ValidationFailure(RuntimeError):
    pass


async def bounded_failure_workload(_task):
    raise ValidationFailure("sensitive-sentinel-" + "x" * 1_000)


class ContentFailureWorkload:
    def __init__(self, marker):
        self.failure = RuntimeError(marker)
        self.task_ids = []
        self.hook_errors = []
        self.hook_finished = asyncio.Event()

    async def fail(self, task):
        self.task_ids.append(task.task_id)
        raise self.failure

    async def on_failed(self, _task_id, error):
        self.hook_errors.append(error)
        self.hook_finished.set()


class BlockingFailureHook:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.failure_hooks = []
        self.cancellation_hooks = []

    async def fail(self, _task):
        raise RuntimeError("forced terminalization race")

    async def on_failed(self, task_id, error):
        self.failure_hooks.append((task_id, type(error).__name__))
        self.entered.set()
        await self.release.wait()
        self.finished.set()

    async def on_cancelled(self, task_id):
        self.cancellation_hooks.append(task_id)
