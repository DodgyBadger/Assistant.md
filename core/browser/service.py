"""Shared browser resource admission, owned by the application runtime."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from threading import Lock
from uuid import uuid4
from weakref import WeakKeyDictionary, WeakValueDictionary

from playwright.async_api import BrowserContext

from core.identity import require_current_execution_authority
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskSnapshot,
    get_current_execution_task,
)
from core.runtime.resources import read_cgroup_memory_status
from core.runtime.state import get_runtime_context
from core.settings import (
    get_browser_max_calls_per_turn,
    get_browser_max_concurrent_sessions,
    get_browser_min_memory_headroom_bytes,
    get_browser_navigation_timeout_seconds,
)

from .engine import BrowserEngine
from .network import BrowserEgress, BrowserResolver
from .session import BrowserSession, BrowserView

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
        self._loop = asyncio.get_running_loop()
        self._resolver = BrowserResolver()
        self._egress = BrowserEgress(self._resolver)
        self._proxy: str | None = None
        self._network_lock = asyncio.Lock()
        self._engine_lock = asyncio.Lock()
        self._engine: BrowserEngine | None = None
        self._sessions: dict[tuple[str, str], _Retained] = {}
        self._session_locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = (
            WeakValueDictionary()
        )

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

    async def proxy_url(self) -> str:
        self._require_loop()
        async with self._network_lock:
            self._require_open()
            if self._proxy is None:
                self._proxy = await self._egress.start()
            return self._proxy

    async def read_current(self, url: str) -> BrowserView:
        key = await self._current_chat()
        lock = self._session_lock(key)
        async with asyncio.timeout(20), lock:
            self._require_open()
            await self._current_chat()
            if url:
                await self._resolver.resolve(url)
            retained = self._sessions.get(key)
            if retained is None:
                if not url:
                    raise BrowserAdmissionError(
                        "no_current_page", "Open an interactive URL first"
                    )
                # The service owns construction through cancellation so remote
                # context creation cannot be abandoned between IPC and cleanup.
                building = asyncio.create_task(self._create(key))
                cancelled: asyncio.CancelledError | None = None
                while not building.done():
                    try:
                        await asyncio.shield(building)
                    except asyncio.CancelledError as exc:
                        cancelled = exc
                    except Exception:
                        break
                try:
                    retained = building.result()
                except BaseException as exc:
                    if cancelled is not None:
                        cancelled.add_note(
                            f"Browser construction failed during cancellation: {type(exc).__name__}"
                        )
                        raise cancelled from exc
                    raise
                if cancelled is not None:
                    await self._close_after_failure(key, cancelled)
                    raise cancelled
            try:
                return await retained.session.read(
                    url, int(get_browser_navigation_timeout_seconds() * 1000)
                )
            except BaseException as error:
                # Keep an existing usable page for a later explicit retry, but
                # never leave an unreachable failed first navigation admitted.
                if retained.session.revision == 0:
                    await self._close_after_failure(key, error)
                raise

    async def _close_after_failure(
        self, key: tuple[str, str], error: BaseException
    ) -> None:
        try:
            await self._close_key(key)
        except BaseException as cleanup_error:
            error.add_note(f"Browser cleanup failed: {type(cleanup_error).__name__}")

    async def close_current(self) -> dict[str, str]:
        key = await self._current_chat()
        async with asyncio.timeout(15), self._session_lock(key):
            await self._current_chat()
            await self._close_key(key)
        return {"status": "closed"}

    @asynccontextmanager
    async def deleting_chat(
        self, principal_id: str, session_id: str
    ) -> AsyncIterator[None]:
        """Hold browser revocation through the authoritative session deletion."""
        self._require_loop()
        authority = require_current_execution_authority()
        if authority.principal_id != principal_id:
            raise BrowserAdmissionError(
                "unsupported_context", "Browser session owner does not match"
            )
        key = (principal_id, session_id)
        async with asyncio.timeout(15), self._session_lock(key):
            get_runtime_context().chat_session_access.require_session(session_id)
            await self._close_key(key)
            yield

    async def _current_chat(self) -> tuple[str, str]:
        self._require_loop()
        self._require_open()
        runtime = get_runtime_context()
        authority = require_current_execution_authority()
        current = get_current_execution_task()
        if (
            current is None
            or current.kind != "chat"
            or not current.scope.startswith("chat_session:")
        ):
            raise BrowserAdmissionError(
                "unsupported_context",
                "Interactive browsing requires an authorized chat task",
            )
        task = await runtime.execution_task_access.get_task(current.task_id)
        if (
            runtime.browser is not self
            or task is None
            or task.is_terminal
            or task.cancel_requested
            or task.principal_id != authority.principal_id
        ):
            raise BrowserAdmissionError(
                "unsupported_context", "Interactive browser task is no longer active"
            )
        session_id = task.scope.removeprefix("chat_session:")
        runtime.chat_session_access.require_session(session_id)
        return authority.principal_id, session_id

    def _session_lock(self, key: tuple[str, str]) -> asyncio.Lock:
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    def _require_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise BrowserAdmissionError(
                "wrong_runtime_loop",
                "Browser operations must run on the application runtime loop",
            )

    async def _create(self, key: tuple[str, str]) -> _Retained:
        # Include lock admission, driver startup and IPC allocation. The owned
        # task still completes its bounded cleanup before its caller returns.
        try:
            async with asyncio.timeout(20):
                return await self._allocate(key)
        except TimeoutError:
            with self._lock:
                self._closed = True
            raise

    async def _allocate(self, key: tuple[str, str]) -> _Retained:
        stack = AsyncExitStack()
        try:
            stack.enter_context(self.reserve())
            async with self._engine_lock:
                self._require_open()
                if self._engine is None:
                    self._engine = BrowserEngine()
                    await self._engine.start(await self.proxy_url())
                browser = self._engine.browser
                if browser is None or not browser.is_connected():
                    raise BrowserAdmissionError(
                        "browser_unavailable", "Browser process is unavailable"
                    )
                try:
                    async with asyncio.timeout(5):
                        context = await browser.new_context(
                            accept_downloads=False,
                            service_workers="block",
                            viewport={"width": 1280, "height": 800},
                        )
                        stack.push_async_callback(self._close_context, context)
                        session = BrowserSession(context, self._resolver)
                        await session.prepare()
                except TimeoutError:
                    logger.set_sinks(["validation"]).info(
                        "Browser context construction deadline",
                        data={
                            "event": "browser_context_creation_failed",
                            "status": "failed",
                            "reason": "construction_deadline",
                            "session_id": key[1],
                        },
                    )
                    # An IPC allocation may have completed remotely without a
                    # returned handle. Stop admission until engine teardown.
                    with self._lock:
                        self._closed = True
                    raise
                self._require_open()
                retained = _Retained(session, stack)
                self._sessions[key] = retained
                logger.add_sink("validation").info(
                    "Interactive browser opened",
                    data={
                        "event": "browser_context_opened",
                        "status": "completed",
                        "session_id": key[1],
                        "principal_id": key[0],
                        "browser_id": session.browser_id,
                    },
                )
                return retained
        except BaseException as error:
            try:
                await stack.aclose()
            except BaseException as cleanup_error:
                error.add_note(
                    f"Context allocation cleanup failed: {type(cleanup_error).__name__}"
                )
            try:
                async with self._engine_lock:
                    if (
                        self._closed or not self._sessions
                    ) and self._engine is not None:
                        await self._close_engine()
            except BaseException as cleanup_error:
                error.add_note(
                    f"Browser allocation cleanup failed: {type(cleanup_error).__name__}"
                )
            raise

    async def _close_context(self, context: BrowserContext) -> None:
        try:
            async with asyncio.timeout(5):
                await context.close()
        except BaseException:
            # A resource whose closure is uncertain must never be followed by
            # further admission, even while the exit stack releases its lease.
            with self._lock:
                self._closed = True
            raise

    async def _close_key(self, key: tuple[str, str]) -> None:
        retained = self._sessions.pop(key, None)
        try:
            if retained is not None:
                await retained.stack.aclose()
                logger.add_sink("validation").info(
                    "Interactive browser closed",
                    data={
                        "event": "browser_context_closed",
                        "status": "completed",
                        "session_id": key[1],
                        "principal_id": key[0],
                        "browser_id": retained.session.browser_id,
                    },
                )
        finally:
            async with self._engine_lock:
                if (self._closed or not self._sessions) and self._engine is not None:
                    await self._close_engine()

    async def _close_engine(self) -> None:
        engine = self._engine
        if engine is not None:
            try:
                async with asyncio.timeout(5):
                    await engine.close()
            except BaseException:
                with self._lock:
                    self._closed = True
                raise
            self._engine = None

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
            except BaseException:
                # Retrieve and rethrow the owned cleanup result below so a
                # previous caller cancellation retains its diagnostics too.
                break
        try:
            draining.result()
        except BaseException as exc:
            if cancellation is not None:
                cancellation.add_note(f"Browser cleanup failed: {type(exc).__name__}")
                raise cancellation from exc
            raise
        if cancellation is not None:
            raise cancellation

    async def _drain(self) -> None:
        failures: list[BaseException] = []
        # Each independently owned component gets a teardown attempt even if
        # another fails. A failed release is reported after those attempts.
        for key in list(self._sessions):
            try:
                async with asyncio.timeout(20), self._session_lock(key):
                    await self._close_key(key)
            except BaseException as exc:
                failures.append(exc)
        try:
            async with asyncio.timeout(20), self._engine_lock:
                await self._close_engine()
        except BaseException as exc:
            failures.append(exc)
        for cleanup in (self._egress.close, self._resolver.close):
            try:
                async with asyncio.timeout(8):
                    await cleanup()
            except BaseException as exc:
                failures.append(exc)
        try:
            async with asyncio.timeout(5):
                while True:
                    with self._lock:
                        if not self._leases:
                            break
                    await asyncio.sleep(0.01)
        except TimeoutError as exc:
            failures.append(exc)
        if failures:
            raise BaseExceptionGroup("Browser resource cleanup is incomplete", failures)

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


@dataclass
class _Retained:
    session: BrowserSession
    stack: AsyncExitStack
