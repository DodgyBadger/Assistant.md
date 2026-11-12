"""Shared browser resource admission, owned by the application runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock
from uuid import uuid4
from weakref import WeakKeyDictionary

from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskSnapshot,
    get_current_execution_task,
)
from core.runtime.resources import read_cgroup_memory_status
from core.settings import (
    get_browser_max_calls_per_turn,
    get_browser_max_concurrent_sessions,
    get_browser_min_memory_headroom_bytes,
)

logger = UnifiedLogger(tag="browser-service")


class BrowserService:
    """Count task calls and allocated resources without queueing for capacity.

    Admission bookkeeping is shared across callers; engine objects introduced
    by retained browsing must stay on the runtime event loop.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._leases: set[str] = set()
        self._task_calls: dict[str, int] = {}
        self._unscoped_calls: WeakKeyDictionary[asyncio.Task[object], int] = (
            WeakKeyDictionary()
        )
        self._closed = False

    def record_call(self) -> None:
        """Apply the same execution-task budget to every agent browser call."""
        limit = get_browser_max_calls_per_turn()
        execution = get_current_execution_task()
        current = asyncio.current_task()
        with self._lock:
            self._require_open()
            if limit <= 0:
                return
            if execution is not None:
                count = self._task_calls.get(execution.task_id, 0) + 1
                self._task_calls[execution.task_id] = count
            elif current is not None:
                count = self._unscoped_calls.get(current, 0) + 1
                self._unscoped_calls[current] = count
            else:
                raise RuntimeError("Browser calls require an asynchronous task")
        if count > limit:
            self._refuse(
                "task_call_limit",
                f"Browser call limit exceeded for this execution task ({limit}). "
                "Use web_extract for static pages or continue in a new scoped task.",
                call_count=count,
                call_limit=limit,
            )

    @contextmanager
    def reserve(self) -> Iterator[None]:
        """Hold capacity until the actual browser resource has been released."""
        lease = self._acquire()
        try:
            yield
        finally:
            with self._lock:
                self._leases.remove(lease)

    def on_task_terminal(self, task: ExecutionTaskSnapshot) -> None:
        """Discard finished task counters without evicting active task budgets."""
        with self._lock:
            self._task_calls.pop(task.task_id, None)

    async def shutdown(self) -> None:
        """Close admission and allow cancelled operations to finish releasing."""
        with self._lock:
            self._closed = True
            self._task_calls.clear()
            self._unscoped_calls.clear()
        # Coordinator shutdown requests cancellation, but a call's finally block
        # may not yet have run. Keep cleanup bounded and owned even if the
        # shutdown caller itself is cancelled.
        draining = asyncio.create_task(self._drain())
        cancellation: asyncio.CancelledError | None = None
        while not draining.done():
            try:
                await asyncio.shield(draining)
            except asyncio.CancelledError as exc:
                cancellation = exc
            except RuntimeError:
                # Retrieve and propagate the drain failure below, preserving a
                # prior caller cancellation with explicit cleanup diagnostics.
                break
        try:
            draining.result()
        except Exception as exc:
            if cancellation is not None:
                cancellation.add_note(f"Browser cleanup failed: {type(exc).__name__}")
                raise cancellation from exc
            raise
        if cancellation is not None:
            raise cancellation

    async def _drain(self) -> None:
        try:
            async with asyncio.timeout(5):
                while True:
                    with self._lock:
                        if not self._leases:
                            return
                    await asyncio.sleep(0.01)
        except TimeoutError as exc:
            raise RuntimeError(
                "Browser resource release was not confirmed within shutdown deadline"
            ) from exc

    def _acquire(self) -> str:
        with self._lock:
            self._require_open()
            # Consult the live limit on every new allocation. Lowering it does
            # not revoke existing leases; it prevents additional admission.
            limit = get_browser_max_concurrent_sessions()
            if len(self._leases) >= limit:
                self._refuse(
                    "capacity_limit",
                    "result_type: capacity\nBrowser capacity is occupied. "
                    "Finish or close the existing browser before requesting another.",
                    active_sessions=len(self._leases),
                    session_limit=limit,
                )
            required = get_browser_min_memory_headroom_bytes()
            if required > 0:
                available = read_cgroup_memory_status().available_bytes
                if available is not None and available < required:
                    self._refuse(
                        "memory_headroom",
                        "Insufficient container memory headroom for Chromium launch: "
                        f"{available // (1024 * 1024)} MiB available, "
                        f"{required // (1024 * 1024)} MiB required.",
                        available_bytes=available,
                        required_bytes=required,
                    )
            lease = uuid4().hex
            self._leases.add(lease)
            return lease

    def _require_open(self) -> None:
        if self._closed:
            raise BrowserAdmissionError("runtime_closed", "Browser runtime is closed")

    @staticmethod
    def _refuse(reason: str, message: str, **fields: int) -> None:
        execution = get_current_execution_task()
        logger.add_sink("validation").info(
            "Browser admission refused",
            data={
                "event": "browser_launch_refused",
                "status": "refused",
                "tool": "browser",
                "reason": reason,
                "task_id": execution.task_id if execution else None,
                **fields,
            },
        )
        raise BrowserAdmissionError(reason, message)


class BrowserAdmissionError(RuntimeError):
    """A bounded, actionable refusal to allocate or use browser resources."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
