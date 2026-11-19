"""Shared browser resource admission, owned by the application runtime."""

from __future__ import annotations

import asyncio
import contextvars
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass, replace
from threading import Lock
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from weakref import WeakKeyDictionary, WeakValueDictionary

from playwright.async_api import BrowserContext, Error

from core.identity import require_current_execution_authority
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskSnapshot,
    get_current_execution_task,
)
from core.runtime.resources import read_cgroup_memory_status
from core.runtime.state import get_runtime_context
from core.settings import (
    get_browser_idle_timeout_seconds,
    get_browser_max_calls_per_turn,
    get_browser_max_concurrent_sessions,
    get_browser_min_memory_headroom_bytes,
    get_browser_navigation_timeout_seconds,
)
from core.web.errors import WebUrlPolicyError

from .engine import BrowserEngine
from .handoff import (
    BrowserHandoff,
    HandoffPublisher,
    HandoffReason,
    HandoffStatus,
    HandoffView,
)
from .human_input import BrowserInputError, HumanInput
from .network import BrowserEgress, BrowserResolver
from .observation import (
    ActionOperation,
    BrowserActionUncertain,
    BrowserInteractionError,
    BrowserReadDeadline,
)
from .operations import BrowserOperations
from .session import BrowserSession, BrowserView
from .surface import BrowserSurface
from .viewer import HumanFrame

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
        self._shutting_down = False
        self._loop = asyncio.get_running_loop()
        self._resolver = BrowserResolver()
        self._egress = BrowserEgress(self._resolver)
        self._proxy: str | None = None
        self._network_lock = asyncio.Lock()
        self._engine_lock = asyncio.Lock()
        self._engine: BrowserEngine | None = None
        self._context_operations = BrowserOperations[None]()
        self._sessions: dict[tuple[str, str], _Retained] = {}
        self._handoff_publishers: dict[str, tuple[object, HandoffPublisher]] = {}
        self._expiry: asyncio.Task[None] | None = None
        self._expiry_wakeup = asyncio.Event()
        self._expiry_failures: list[str] = []
        self._session_locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = (
            WeakValueDictionary()
        )

    def quarantine_cleanup(self) -> None:
        """Reject further work after an owner cannot confirm final teardown."""
        with self._lock:
            self._closed = True
        self._expiry_wakeup.set()
        logger.add_sink("validation").error(
            "Browser cleanup is unconfirmed; admission closed",
            data={"event": "browser_cleanup_unconfirmed", "status": "failed"},
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

    async def validate_target(self, url: str) -> None:
        """Apply the shared bounded DNS policy without blocking browser callers."""
        self._require_loop()
        self._require_open()
        await self._resolver.resolve(url)
        # A lookup that outlives shutdown must not start a new browser after
        # admission has closed. The proxy independently checks and pins each
        # actual connection; this validation does not cache destination approval.
        self._require_open()

    async def handoff_current(self, reason: HandoffReason) -> HandoffView:
        if reason not in {"sign_in", "credential", "verification"}:
            raise BrowserAdmissionError("invalid_handoff", "Invalid handoff reason")
        key = await self._current_chat()
        handoff: BrowserHandoff | None = None
        try:
            async with asyncio.timeout(10), self._session_lock(key):
                await self._current_chat()
                await self._expire_key_if_idle(key)
                retained = self._sessions.get(key)
                if (
                    retained is None
                    or not retained.session.has_retained_document()
                    or not retained.session.is_usable()
                ):
                    raise BrowserAdmissionError(
                        "no_current_page", "Open a usable interactive page first"
                    )
                previous = retained.handoff
                if previous is not None and previous.status in {"waiting", "human"}:
                    raise BrowserAdmissionError(
                        "human_control_active", "Browser already awaits human control"
                    )
                observation = retained.session.observation
                if observation is None or observation.broken:
                    raise BrowserAdmissionError(
                        "browser_closed", "Browser observations are unavailable"
                    )
                try:
                    await observation.invalidate()
                except BaseException as error:
                    await self._close_after_failure(key, error)
                    raise
                await self._current_chat()
                task = get_current_execution_task()
                if task is None:
                    raise BrowserAdmissionError(
                        "unsupported_context", "An active chat task is required"
                    )
                handoff = BrowserHandoff(
                    task.task_id,
                    retained.session.browser_id,
                    retained.session.page_id,
                    reason,
                )
                retained.handoff = handoff
                retained.session.popup_generation = handoff.handoff_id
                retained.session.popup_enabled = True
                if previous is None or not previous.human_controlled:
                    retained.last_activity = self._loop.time()
                await self._publish_handoff(key, handoff)
            # Neither the wait nor metadata polling holds the chat lock or
            # extends the single inactivity deadline. Stop cannot cancel this
            # future and accidentally transfer ownership.
            outcome = await asyncio.shield(handoff.completed)
        except BaseException:
            if handoff is not None and handoff.status in {"waiting", "human"}:
                handoff.finish("stopped")
                self._handoff_event(key, handoff)
            raise
        if outcome != "returned":
            raise BrowserAdmissionError(
                (
                    "browser_expired"
                    if outcome == "expired"
                    else (
                        "browser_cleanup_unconfirmed"
                        if outcome == "cleanup_unconfirmed"
                        else "browser_closed"
                    )
                ),
                "Browser handoff ended without returning control; open a fresh session",
            )
        return handoff.view()

    def _require_agent_control(self, key: tuple[str, str]) -> None:
        retained = self._sessions.get(key)
        if (
            retained is not None
            and retained.handoff is not None
            and retained.handoff.human_controlled
        ):
            raise BrowserAdmissionError(
                "human_control_active",
                "Browser remains human-owned. Request handoff to resume a stopped "
                "session, or use operation=close to discard it before opening a "
                "fresh URL. Reloading chat does not release browser ownership.",
            )

    async def handoff_state(self, session_id: str) -> HandoffView:
        key = self._human_chat(session_id)
        async with asyncio.timeout(10), self._session_lock(key):
            retained, handoff = await self._human_handoff(key)
            return handoff.view()

    async def close_handoff(self, session_id: str, handoff_id: str) -> HandoffView:
        key = self._human_chat(session_id)
        async with asyncio.timeout(15), self._session_lock(key):
            retained, handoff = await self._human_handoff(key, handoff_id)
            if not handoff.human_controlled:
                raise BrowserAdmissionError(
                    "stale_handoff", "This handoff no longer owns the browser"
                )
            await self._end_retained(key, retained, "viewer")
            return handoff.view()

    async def claim_handoff(self, session_id: str, handoff_id: str) -> HandoffView:
        key = self._human_chat(session_id)
        async with asyncio.timeout(10), self._session_lock(key):
            retained, handoff = await self._human_handoff(key, handoff_id)
            await self._require_handoff_task(key, handoff)
            if handoff.status == "human":
                return handoff.view()
            if handoff.status != "waiting":
                raise BrowserAdmissionError(
                    "stale_handoff", "Handoff is no longer waiting"
                )
            handoff.status = "human"
            retained.last_activity = self._loop.time()
            await self._publish_handoff(key, handoff)
            return handoff.view()

    async def return_handoff(self, session_id: str, handoff_id: str) -> HandoffView:
        key = self._human_chat(session_id)
        async with asyncio.timeout(10), self._session_lock(key):
            retained, handoff = await self._human_handoff(key, handoff_id)
            if handoff.status == "returned":
                return handoff.view()
            await self._require_handoff_task(key, handoff)
            if handoff.status != "human":
                raise BrowserAdmissionError(
                    "stale_handoff", "Claim human control before returning it"
                )
            session = retained.session
            if session.human_busy:
                raise BrowserAdmissionError(
                    "browser_busy", "Browser work has not settled"
                )
            await self._human_surface(key, retained)
            # New page admission stays sealed through inspection and retirement.
            session.popup_enabled = False
            try:
                surfaces = session.surfaces
                selected = session.human_surface
                if (
                    session.human_busy
                    or any(
                        surface.viewer is None or not surface.viewer.settled_input
                        for surface in surfaces
                    )
                    or selected.viewer is None
                    or not selected.viewer.can_return
                ):
                    raise BrowserAdmissionError(
                        "browser_busy", "Inspect pending input before returning control"
                    )
                selection = session.selection_revision
                revisions = tuple(surface.document_revision for surface in surfaces)
                logger.set_sinks(["validation"]).info(
                    "Browser handback inspection started",
                    data={
                        "event": "browser_handback_check_started",
                        "session_id": key[1],
                        "task_id": handoff.task_id,
                        "handoff_id": handoff.handoff_id,
                        "browser_id": handoff.browser_id,
                    },
                )
                async with asyncio.timeout(3):
                    for surface in surfaces:
                        if (
                            surface.observation is None
                            or not await surface.observation.credentials_clear()
                        ):
                            raise BrowserAdmissionError(
                                "credentials_pending",
                                "Submit or clear credential fields before returning control",
                            )
                current, current_handoff = await self._human_handoff(
                    key, handoff_id, check_idle=False
                )
                await self._require_handoff_task(key, handoff)
                if (
                    current is not retained
                    or current_handoff is not handoff
                    or handoff.status != "human"
                    or session.selection_revision != selection
                    or session.surfaces != surfaces
                    or tuple(surface.document_revision for surface in surfaces)
                    != revisions
                ):
                    raise BrowserAdmissionError(
                        "stale_handoff", "Handoff is no longer current"
                    )
                root_revision = session.document_revision
                try:
                    await session.close_popup()
                except BaseException as error:
                    await self._close_after_failure(key, error)
                    raise
                await self._human_handoff(key, handoff_id, check_idle=False)
                await self._require_handoff_task(key, handoff)
                if (
                    session.document_revision != root_revision
                    or session.human_busy
                    or session.popup is not None
                    or any(
                        page is not session.page and not page.is_closed()
                        for page in session.context.pages
                    )
                ):
                    raise BrowserAdmissionError(
                        "stale_handoff",
                        "Inspect the original page before returning control",
                    )
                handoff.status = "returned"
                retained.last_activity = self._loop.time()
                try:
                    await self._publish_handoff(key, handoff)
                finally:
                    handoff.finish("returned")
                return handoff.view()
            finally:
                if handoff.status != "returned" and self._sessions.get(key) is retained:
                    session.popup_enabled = True

    @asynccontextmanager
    async def _viewer_slot(self, key: tuple[str, str]) -> AsyncIterator[_Retained]:
        retained = self._sessions.get(key)
        if retained is None or retained.session.viewer is None:
            raise BrowserAdmissionError("no_handoff", "Browser viewer is unavailable")
        if retained.session.human_busy:
            raise BrowserInputError("browser_busy")
        retained.session.human_io_pending = True
        try:
            yield retained
        finally:
            retained.session.human_io_pending = False

    async def _human_surface(
        self, key: tuple[str, str], retained: _Retained
    ) -> BrowserSurface:
        try:
            await retained.session.sync_popup()
            return retained.session.human_surface
        except BrowserInputError as error:
            if error.retire:
                await self._close_after_failure(key, error)
            raise
        except BaseException as error:
            await self._close_after_failure(key, error)
            raise

    async def _viewer_handoff(
        self,
        key: tuple[str, str],
        handoff_id: str,
        expected: _Retained,
        *,
        check_idle: bool = True,
    ) -> BrowserHandoff:
        current, handoff = await self._human_handoff(
            key, handoff_id, check_idle=check_idle
        )
        await self._require_handoff_task(key, handoff)
        if (
            current is not expected
            or self._sessions.get(key) is not expected
            or handoff.status != "human"
        ):
            raise BrowserAdmissionError(
                "stale_handoff", "Human control is no longer current"
            )
        return handoff

    async def frame_handoff(self, session_id: str, handoff_id: str) -> HumanFrame:
        key = self._human_chat(session_id)
        async with self._viewer_slot(key) as retained:
            async with self._viewer_lock(key):
                handoff = await self._viewer_handoff(key, handoff_id, retained)
                surface = await self._human_surface(key, retained)
                viewer = surface.viewer
                assert viewer is not None
                viewer.bind(handoff.handoff_id)
                page = surface.page
                if page is None:
                    raise BrowserInputError("frame_unavailable")
                revision, url = surface.document_revision, page.url
                selection = retained.session.selection_revision
                frame = await viewer.capture()
                # Frames are passive: crossing expiry does not extend retention.
                await self._viewer_handoff(key, handoff_id, retained)
                if (
                    revision != surface.document_revision
                    or url != page.url
                    or selection != retained.session.selection_revision
                    or surface is not retained.session.human_surface
                ):
                    viewer.invalidate()
                    raise BrowserInputError("frame_unavailable")
                popup = retained.session.popup
                return replace(
                    frame,
                    site_origin=_frame_origin(url),
                    root_page_id=retained.session.page_id,
                    popup_page_id=popup.page_id if popup is not None else "",
                    selection_revision=selection,
                    window_kind="sign_in" if surface is popup else "original",
                    popup_notice=retained.session.popup_notice,
                )

    async def input_handoff(
        self,
        session_id: str,
        handoff_id: str,
        reader: Callable[[], Awaitable[HumanInput]],
    ) -> dict[str, int | bool | str]:
        key = self._human_chat(session_id)
        async with self._viewer_slot(key) as retained:
            async with self._viewer_lock(key):
                handoff = await self._viewer_handoff(key, handoff_id, retained)
                surface = await self._human_surface(key, retained)
                viewer = surface.viewer
                assert viewer is not None
                viewer.bind(handoff.handoff_id)
                selection = retained.session.selection_revision
                # Reserve admission before reading any private bytes. Reading,
                # geometry and native dispatch each have their own deadline.
                command = await reader()
                geometry = await viewer.prepare_input()
                await self._viewer_handoff(key, handoff_id, retained, check_idle=False)
                if (
                    selection != retained.session.selection_revision
                    or surface is not retained.session.human_surface
                ):
                    viewer.invalidate()
                    raise BrowserInputError("stale_input")
                try:
                    response = await viewer.dispatch(command, geometry)
                except BrowserInputError as error:
                    if error.retire:
                        await self._close_after_failure(key, error)
                    raise
                except asyncio.CancelledError as error:
                    if viewer.unsafe:
                        await self._close_after_failure(key, error)
                    raise
                await self._viewer_handoff(key, handoff_id, retained, check_idle=False)
                retained.last_activity = self._loop.time()
                return response

    async def select_handoff_page(
        self, session_id: str, handoff_id: str, page_id: str
    ) -> dict[str, str | int]:
        key = self._human_chat(session_id)
        async with self._viewer_slot(key) as retained:
            async with self._viewer_lock(key):
                await self._viewer_handoff(key, handoff_id, retained)
                await self._human_surface(key, retained)
                await self._viewer_handoff(key, handoff_id, retained, check_idle=False)
                retained.session.select(page_id)
                retained.last_activity = self._loop.time()
                return {
                    "page_id": page_id,
                    "selection_revision": retained.session.selection_revision,
                }

    @asynccontextmanager
    async def _viewer_lock(self, key: tuple[str, str]) -> AsyncIterator[None]:
        lock = self._session_lock(key)
        try:
            async with asyncio.timeout(5):
                await lock.acquire()
        except TimeoutError:
            raise BrowserInputError("browser_busy") from None
        try:
            yield
        finally:
            lock.release()

    def _human_chat(self, session_id: str) -> tuple[str, str]:
        self._require_loop()
        self._require_open()
        authority = require_current_execution_authority()
        runtime = get_runtime_context()
        if runtime.browser is not self or not session_id or len(session_id) > 256:
            raise BrowserAdmissionError(
                "stale_handoff", "Browser handoff is unavailable"
            )
        try:
            runtime.chat_session_access.require_session(session_id)
        except LookupError:
            raise BrowserAdmissionError(
                "no_handoff", "Browser handoff is unavailable"
            ) from None
        return authority.principal_id, session_id

    async def _human_handoff(
        self,
        key: tuple[str, str],
        handoff_id: str | None = None,
        *,
        check_idle: bool = True,
    ) -> tuple[_Retained, BrowserHandoff]:
        self._human_chat(key[1])
        # The return path already checked admission before its bounded guard
        # and still owns this lock. That in-flight interval is not idle time.
        if check_idle:
            await self._expire_key_if_idle(key)
        retained = self._sessions.get(key)
        if retained is None or retained.handoff is None:
            raise BrowserAdmissionError("no_handoff", "Browser handoff is unavailable")
        handoff = retained.handoff
        if (handoff_id is not None and handoff.handoff_id != handoff_id) or (
            handoff.browser_id != retained.session.browser_id
            or handoff.page_id != retained.session.page_id
        ):
            raise BrowserAdmissionError(
                "stale_handoff", "Browser handoff is no longer current"
            )
        if not retained.session.is_usable():
            await self._close_key(key)
            raise BrowserAdmissionError("browser_closed", "Browser page is unavailable")
        return retained, handoff

    async def _require_handoff_task(
        self, key: tuple[str, str], handoff: BrowserHandoff
    ) -> None:
        task = await get_runtime_context().execution_task_access.get_task(
            handoff.task_id
        )
        if (
            task is None
            or task.is_terminal
            or task.cancel_requested
            or task.principal_id != key[0]
            or task.kind != "chat"
            or task.scope != f"chat_session:{key[1]}"
            or handoff.status not in {"waiting", "human"}
        ):
            raise BrowserAdmissionError(
                "stale_handoff", "The owning handoff task is no longer active"
            )

    @asynccontextmanager
    async def handoff_events(
        self, task_id: str, publisher: HandoffPublisher
    ) -> AsyncIterator[None]:
        """Own one exact chat attempt's bounded metadata observer."""
        self._require_loop()
        if task_id in self._handoff_publishers:
            raise RuntimeError("Chat task already has a browser event observer")
        subscription = (object(), publisher)
        self._handoff_publishers[task_id] = subscription
        try:
            yield
        finally:
            if self._handoff_publishers.get(task_id) is subscription:
                del self._handoff_publishers[task_id]

    async def _publish_handoff(
        self, key: tuple[str, str], handoff: BrowserHandoff
    ) -> None:
        await get_runtime_context().task_coordinator.publish_progress(
            handoff.task_id,
            metadata={"browser_handoff": handoff.view().as_metadata()},
            health_status=(
                "attention_required" if handoff.human_controlled else "healthy"
            ),
        )
        self._handoff_event(key, handoff)
        subscription = self._handoff_publishers.get(handoff.task_id)
        if subscription is not None:
            try:
                async with asyncio.timeout(1):
                    await subscription[1](
                        handoff.view(),
                        lambda: self._handoff_publishers.get(handoff.task_id)
                        is subscription,
                    )
            except Exception as error:
                # Chat projection is advisory. Observer failure cannot revoke
                # ownership or turn a successful handback into a failed one.
                logger.warning(
                    "Browser handoff observer failed",
                    data={
                        "event": "browser_handoff_observer_failed",
                        "task_id": handoff.task_id,
                        "error_type": type(error).__name__,
                    },
                )

    @staticmethod
    def _handoff_event(key: tuple[str, str], handoff: BrowserHandoff) -> None:
        logger.add_sink("validation").info(
            "Browser handoff state changed",
            data={
                "event": "browser_handoff_changed",
                "session_id": key[1],
                "principal_id": key[0],
                **handoff.view().as_metadata(),
            },
        )

    async def read_current(self, url: str) -> BrowserView:
        key = await self._current_chat()
        async with self._agent_operation(key):
            self._require_open()
            await self._current_chat()
            logger.set_sinks(["validation"]).info(
                "Browser read started",
                data={
                    "event": "browser_read_started",
                    "status": "started",
                    "session_id": key[1],
                    "principal_id": key[0],
                },
            )
            had_page = key in self._sessions
            if await self._retire_disconnected_engine() and had_page:
                raise BrowserAdmissionError(
                    "browser_closed",
                    "Browser process exited; open a new interactive URL",
                )
            expired = await self._expire_key_if_idle(key)
            if expired and not url:
                raise BrowserAdmissionError(
                    "browser_expired",
                    "Browser expired after inactivity; open a new URL",
                )
            if url:
                try:
                    await self._resolver.resolve(url)
                except WebUrlPolicyError:
                    raise BrowserAdmissionError(
                        "destination_denied", "Browser destination is not allowed"
                    ) from None
            retained = self._sessions.get(key)
            if retained is not None and not retained.session.is_usable():
                await self._close_key(key)
                raise BrowserAdmissionError(
                    "browser_closed", "Browser page closed; open a new interactive URL"
                )
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
                retained.last_activity = self._loop.time()
                view = await retained.session.read(
                    url, int(get_browser_navigation_timeout_seconds() * 1000)
                )
                return view
            except BaseException as error:
                # Keep an existing usable page for a later explicit retry, but
                # never leave an unreachable failed first navigation admitted.
                retained_deadline = (
                    isinstance(error, BrowserReadDeadline)
                    and error.page_retained
                    and retained.session.has_retained_document()
                    and not retained.session.needs_disposal()
                )
                if isinstance(error, BrowserReadDeadline):
                    if not retained_deadline:
                        error.page_retained = error.help_eligible = False
                    execution = get_current_execution_task()
                    logger.add_sink("validation").info(
                        "Browser read deadline",
                        data={
                            "event": "browser_read_deadline",
                            "status": "failed",
                            "phase": "navigation",
                            "session_id": key[1],
                            "principal_id": key[0],
                            "task_id": execution.task_id if execution else None,
                            "browser_id": retained.session.browser_id,
                            "page_retained": retained_deadline,
                            "help_eligible": error.help_eligible,
                        },
                    )
                if (
                    retained.session.revision == 0 and not retained_deadline
                ) or retained.session.needs_disposal():
                    await self._close_after_failure(key, error)
                raise
            finally:
                # Failed/cancelled real operations also cease being active at
                # completion. A rejected URL preflight does not refresh this.
                retained.last_activity = self._loop.time()

    async def _close_after_failure(
        self, key: tuple[str, str], error: BaseException
    ) -> None:
        try:
            await self._close_key(key)
        except asyncio.CancelledError:
            if not isinstance(error, asyncio.CancelledError):
                raise
            error.add_note("Browser cleanup was interrupted by repeated cancellation")
        except BaseException as cleanup_error:
            error.add_note(f"Browser cleanup failed: {type(cleanup_error).__name__}")

    async def close_current(self) -> dict[str, str]:
        key = await self._current_chat()
        async with asyncio.timeout(15), self._session_lock(key):
            await self._current_chat()
            await self._end_retained(key, self._sessions.get(key), "agent")
        return {"status": "closed"}

    async def _end_retained(
        self,
        key: tuple[str, str],
        retained: _Retained | None,
        source: Literal["agent", "viewer"],
    ) -> None:
        if retained is not None:
            logger.add_sink("validation").info(
                "Browser session termination requested",
                data={
                    "event": "browser_session_close_requested",
                    "status": "started",
                    "source": source,
                    "session_id": key[1],
                    "principal_id": key[0],
                    "browser_id": retained.session.browser_id,
                },
            )
            handoff = retained.handoff
            current = get_current_execution_task()
            if (
                handoff is not None
                and handoff.human_controlled
                and (current is None or current.task_id != handoff.task_id)
            ):
                coordinator = get_runtime_context().task_coordinator
                owner = await coordinator.get_task(handoff.task_id)
                if (
                    self._sessions.get(key) is not retained
                    or retained.handoff is not handoff
                ):
                    raise BrowserAdmissionError(
                        "stale_handoff", "The browser changed during termination"
                    )
                if (
                    owner is not None
                    and not owner.is_terminal
                    and (
                        owner.principal_id == key[0]
                        and owner.kind == "chat"
                        and owner.scope == f"chat_session:{key[1]}"
                    )
                ):
                    # Request only: waiting here for task termination could
                    # deadlock its finally path against our held session lock.
                    await coordinator.cancel_task(
                        handoff.task_id, reason="browser_session_closed"
                    )
                if (
                    self._sessions.get(key) is not retained
                    or retained.handoff is not handoff
                ):
                    raise BrowserAdmissionError(
                        "stale_handoff", "The browser changed during termination"
                    )
        await self._close_key(key)

    async def act_current(
        self, operation: ActionOperation, reference: str, value: str, scroll_y: int
    ) -> BrowserView:
        if (
            operation not in {"click", "fill", "select", "scroll"}
            or not reference
            or len(reference) > 128
            or len(value) > 4096
            or (operation not in {"fill", "select"} and value)
            or (operation == "select" and len(value) > 80)
            or (
                operation == "scroll" and (not -800 <= scroll_y <= 800 or scroll_y == 0)
            )
            or (operation != "scroll" and scroll_y != 0)
        ):
            raise BrowserInteractionError("invalid_action")
        key = await self._current_chat()
        async with self._agent_operation(key):
            await self._current_chat()
            had_page = key in self._sessions
            if await self._retire_disconnected_engine() and had_page:
                raise BrowserInteractionError("stale_reference")
            if await self._expire_key_if_idle(key):
                raise BrowserInteractionError("stale_reference")
            retained = self._sessions.get(key)
            if retained is None or not retained.session.is_usable():
                raise BrowserInteractionError("stale_reference")
            logger.add_sink("validation").info(
                "Browser action started",
                data={
                    "event": "browser_action_started",
                    "status": "started",
                    "session_id": key[1],
                    "browser_id": retained.session.browser_id,
                    "operation": operation,
                },
            )
            retained.last_activity = self._loop.time()
            try:
                result = await retained.session.act(
                    operation,
                    reference,
                    value,
                    scroll_y,
                    int(get_browser_navigation_timeout_seconds() * 1000),
                )
                logger.add_sink("validation").info(
                    "Browser action completed",
                    data={
                        "event": "browser_action_completed",
                        "status": "completed",
                        "session_id": key[1],
                        "browser_id": retained.session.browser_id,
                        "operation": operation,
                    },
                )
                return result
            except BaseException as error:
                if retained.session.needs_disposal():
                    await self._close_after_failure(key, error)
                logger.add_sink("validation").info(
                    "Browser action failed",
                    data={
                        "event": "browser_action_failed",
                        "status": "failed",
                        "session_id": key[1],
                        "browser_id": retained.session.browser_id,
                        "operation": operation,
                        "reason": (
                            error.reason
                            if isinstance(error, BrowserInteractionError)
                            else "interrupted"
                        ),
                    },
                )
                if isinstance(error, BrowserInteractionError) and error.reason in {
                    "action_uncertain",
                    "browser_unresponsive",
                }:
                    raise BrowserActionUncertain() from None
                raise
            finally:
                retained.last_activity = self._loop.time()

    @asynccontextmanager
    async def _agent_operation(self, key: tuple[str, str]) -> AsyncIterator[None]:
        lock = self._session_lock(key)
        try:
            async with asyncio.timeout(20):
                await lock.acquire()
        except TimeoutError:
            raise BrowserAdmissionError(
                "browser_busy",
                "Browser operation is still in progress; wait before trying again",
            ) from None
        try:
            self._require_agent_control(key)
            # Construction, DNS, navigation, observation and cleanup each own
            # their deadline. Queueing must not consume navigation's interval.
            yield
        finally:
            lock.release()

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
        deadline = asyncio.timeout(20)
        try:
            async with deadline:
                return await self._allocate(key)
        except TimeoutError:
            # Nested startup deadlines may have confirmed full cleanup. Only
            # our own overall deadline implies an unconfirmed allocation here.
            if deadline.expired():
                with self._lock:
                    self._closed = True
            raise

    async def _allocate(self, key: tuple[str, str]) -> _Retained:
        stack = AsyncExitStack()
        try:
            await self._retire_disconnected_engine()
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
                        stack.push_async_callback(session.dispose)
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
                retained = _Retained(session, stack, self._loop.time())
                self._sessions[key] = retained
                if self._expiry is None or self._expiry.done():
                    self._expiry = asyncio.create_task(
                        self._expire_idle(), context=contextvars.Context()
                    )
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
                try:
                    await self._context_operations.run(context.close())
                except Error:
                    # A browser process that has exited has already destroyed
                    # its contexts. Engine teardown still owns driver cleanup.
                    if context.browser is not None and context.browser.is_connected():
                        raise
        except BaseException:
            # A resource whose closure is uncertain must never be followed by
            # further admission, even while the exit stack releases its lease.
            with self._lock:
                self._closed = True
            raise

    async def _close_key(
        self, key: tuple[str, str], outcome: HandoffStatus = "closed"
    ) -> None:
        retained = self._sessions.pop(key, None)
        completed = False
        try:
            try:
                if retained is not None:
                    await self._release_retained(key, retained)
            finally:
                async with self._engine_lock:
                    if (
                        self._closed or not self._sessions
                    ) and self._engine is not None:
                        await self._close_engine()
            completed = True
        finally:
            primary = sys.exception()
            if retained is not None:
                try:
                    await self._settle_handoff(
                        key, retained, outcome if completed else "cleanup_unconfirmed"
                    )
                except asyncio.CancelledError:
                    if not isinstance(primary, asyncio.CancelledError):
                        raise
                    primary.add_note("Handoff publication interrupted during cleanup")
                except Exception as publication_error:
                    if primary is None:
                        raise
                    primary.add_note(
                        f"Handoff publication failed: {type(publication_error).__name__}"
                    )

    async def _settle_handoff(
        self, key: tuple[str, str], retained: _Retained, outcome: HandoffStatus
    ) -> None:
        handoff = retained.handoff
        if handoff is not None:
            # Context and any required engine cleanup have finished or failed.
            # Publish the completed cleanup outcome before waking its agent;
            # failed publication or repeated Stop must never strand the waiter.
            handoff.status = outcome
            try:
                async with asyncio.timeout(1):
                    await self._publish_handoff(key, handoff)
            finally:
                handoff.finish(outcome)

    async def _release_retained(
        self, key: tuple[str, str], retained: _Retained
    ) -> None:
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

    async def _retire_disconnected_engine(self) -> bool:
        engine = self._engine
        if engine is None or engine.browser is None or engine.browser.is_connected():
            return False
        async with self._engine_lock:
            if self._engine is not engine:
                return False
            # A dead shared process destroyed every context. Detach its whole
            # generation before awaiting cleanup; live operations may finish
            # reporting that failure, but cannot reuse these lost resources.
            retired = list(self._sessions.items())
            self._sessions.clear()
            failures: list[Exception] = []
            cancellation: asyncio.CancelledError | None = None
            for key, retained in retired:
                try:
                    await self._release_retained(key, retained)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                except Exception as exc:
                    failures.append(exc)
            try:
                await self._close_engine()
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
            except Exception as exc:
                failures.append(exc)
            outcome: HandoffStatus = (
                "cleanup_unconfirmed"
                if failures or cancellation is not None
                else "closed"
            )
            for key, retained in retired:
                try:
                    await self._settle_handoff(key, retained, outcome)
                except asyncio.CancelledError as exc:
                    cancellation = cancellation or exc
                except Exception as exc:
                    failures.append(exc)
            if cancellation is not None:
                for failure in failures:
                    cancellation.add_note(
                        f"Browser retirement cleanup failed: {type(failure).__name__}"
                    )
                raise cancellation
            if failures:
                with self._lock:
                    self._closed = True
                raise ExceptionGroup("Disconnected browser cleanup failed", failures)
            return True

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

    async def _expire_idle(self) -> None:
        try:
            while not self._shutting_down and self._sessions:
                # Human waits have no foreground browser call to observe a
                # crash. Reuse this owned worker to retire their dead engine.
                if (
                    any(
                        retained.handoff is not None
                        and retained.handoff.human_controlled
                        for retained in self._sessions.values()
                    )
                    and await self._retire_disconnected_engine()
                ):
                    continue
                # Read settings each pass so changes take effect without a
                # restart. Status/frame polling will not touch last_activity.
                for key in list(self._sessions):
                    if self._shutting_down:
                        return
                    lock = self._session_lock(key)
                    try:
                        # Active operations are not idle. Never queue expiry
                        # of other chats behind one busy navigation.
                        async with asyncio.timeout(0.01):
                            await lock.acquire()
                    except TimeoutError:
                        continue
                    try:
                        retained = self._sessions.get(key)
                        if (
                            retained is not None
                            and retained.handoff is not None
                            and retained.handoff.human_controlled
                            and not retained.session.is_usable()
                        ):
                            await self._close_key(key)
                        else:
                            await self._expire_key_if_idle(key)
                    except Exception as exc:
                        # Failed cleanup closes admission, but cannot remove
                        # the only idle owner for other retained sessions.
                        # Their actual resource cleanup still gets an attempt.
                        with self._lock:
                            self._closed = True
                        self._expiry_failures.append(type(exc).__name__)
                        logger.add_sink("validation").error(
                            "Browser inactivity cleanup failed",
                            data={
                                "event": "browser_expiry_failed",
                                "status": "failed",
                                "session_id": key[1],
                                "error_type": type(exc).__name__,
                            },
                        )
                    finally:
                        lock.release()
                try:
                    async with asyncio.timeout(0.25):
                        await self._expiry_wakeup.wait()
                    self._expiry_wakeup.clear()
                except TimeoutError:
                    pass
        except Exception as exc:
            with self._lock:
                self._closed = True
            logger.add_sink("validation").error(
                "Browser inactivity cleanup failed",
                data={
                    "event": "browser_expiry_failed",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                },
            )
            raise

    async def _expire_key_if_idle(self, key: tuple[str, str]) -> bool:
        """Caller owns the chat lock; check the live deadline before reuse."""
        retained = self._sessions.get(key)
        if retained is None or (
            self._loop.time() - retained.last_activity
            < get_browser_idle_timeout_seconds()
        ):
            return False
        await self._close_key(key, "expired")
        logger.add_sink("validation").info(
            "Interactive browser expired",
            data={
                "event": "browser_context_expired",
                "status": "completed",
                "session_id": key[1],
                "principal_id": key[0],
                "browser_id": retained.session.browser_id,
                "reason": "inactivity",
            },
        )
        return True

    def on_task_terminal(self, task: ExecutionTaskSnapshot) -> None:
        """Release task accounting and stop its current human handoff lease."""
        with self._lock:
            self._task_calls.pop(task.task_id, None)
        for key, retained in self._sessions.items():
            handoff = retained.handoff
            if (
                handoff is not None
                and handoff.task_id == task.task_id
                and handoff.status in {"waiting", "human"}
            ):
                handoff.finish("stopped")
                self._handoff_event(key, handoff)

    async def shutdown(self) -> None:
        """Close admission and allow cancelled operations to finish releasing."""
        with self._lock:
            self._closed = True
            self._shutting_down = True
            self._task_calls.clear()
            self._unscoped_calls.clear()
        self._expiry_wakeup.set()
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
        if self._expiry is not None:
            try:
                async with asyncio.timeout(20):
                    await self._expiry
            except BaseException as exc:
                failures.append(exc)
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
        if self._expiry_failures:
            # Historical failures remain visible even if final ownership is
            # now confirmed. Do not turn them into a permanent shutdown error
            # after every real owner closed, or erase actual cleanup failures.
            logger.add_sink("validation").error(
                "Browser shutdown followed failed inactivity cleanup",
                data={
                    "event": "browser_expiry_failure_summary",
                    "status": "failed",
                    "failure_count": len(self._expiry_failures),
                    "error_types": sorted(set(self._expiry_failures)),
                    "final_cleanup": "unconfirmed" if failures else "confirmed",
                },
            )
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
    last_activity: float
    handoff: BrowserHandoff | None = None


def _frame_origin(url: str) -> str:
    """Expose only the actual committed HTTP origin, with no path or credentials."""
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Unsupported origin")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if len(host) > 253:
            raise ValueError("Excessive host")
        if ":" in host:
            host = f"[{host}]"
        port = parsed.port
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            host += f":{port}"
        return f"{parsed.scheme}://{host}"
    except (ValueError, UnicodeError):
        raise BrowserInputError("frame_unavailable") from None
