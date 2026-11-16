"""A retained chat-owned page; browser engine objects stay on their runtime loop."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4
from weakref import WeakSet

from playwright.async_api import (
    BrowserContext,
    Error,
    Frame,
    Page,
    Response,
    Route,
    WebSocketRoute,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.logger import UnifiedLogger
from core.web.errors import WebUrlPolicyError
from core.web.security import sanitize_url_for_log

from .early_navigation import early_frame_key, frame_key
from .human_input import BrowserInputError
from .network import BrowserNetworkError, BrowserResolver
from .observation import (
    ActionOperation,
    BrowserElement,
    BrowserInteractionError,
    BrowserReadDeadline,
    PageObservation,
)
from .operations import BrowserOperations
from .surface import BrowserSurface
from .viewer import HumanViewer


@dataclass(frozen=True)
class BrowserView:
    browser_id: str
    page_id: str
    revision: int
    url: str
    text: str
    observation_ref: str
    elements: list[BrowserElement]
    truncated: bool


@dataclass
class BrowserSession:
    context: BrowserContext
    resolver: BrowserResolver
    browser_id: str = field(default_factory=lambda: uuid4().hex)
    page_id: str = field(default_factory=lambda: uuid4().hex)
    revision: int = 0
    root: BrowserSurface | None = None
    popup: BrowserSurface | None = None
    popup_enabled: bool = False
    popup_generation: str | None = None
    selection_revision: int = 0
    human_io_pending: bool = False
    popup_notice: str = ""
    _selected_popup: bool = False
    _popup_failed: bool = False
    _popup_authorized: bool = False
    _popup_verified: asyncio.Event = field(default_factory=asyncio.Event)
    _verification_waiter: object | None = None
    _bootstrap: dict[str, tuple[str | None, str | None, asyncio.Event]] = field(
        default_factory=dict
    )
    _bootstrap_jobs: set[asyncio.Task[None]] = field(default_factory=set)
    _candidate_key: str | None = None
    _disposing: bool = False
    _popup_preparation: asyncio.Task[None] | None = None
    _popup_operations: BrowserOperations[None] = field(
        default_factory=BrowserOperations
    )
    _rejected_pages: WeakSet[Page] = field(default_factory=WeakSet)
    _popup_closures: set[asyncio.Task[None]] = field(default_factory=set)
    _popup_overflow: bool = False
    _denied_requests: int = 0
    _navigations: BrowserOperations[Response | None] = field(
        default_factory=BrowserOperations
    )

    async def prepare(self) -> None:
        await self.context.route("**/*", self._route)
        await self.context.route_web_socket("**/*", block_websocket)
        self.root = BrowserSurface(await self.context.new_page(), self.page_id)
        await self.root.prepare(self.browser_id)
        self.context.on("page", self._extra_page)

    @property
    def page(self) -> Page | None:
        return self.root.page if self.root is not None else None

    @property
    def observation(self) -> PageObservation | None:
        return self.root.observation if self.root is not None else None

    @property
    def viewer(self) -> HumanViewer | None:
        return self.root.viewer if self.root is not None else None

    @property
    def document_revision(self) -> int:
        return self.root.document_revision if self.root is not None else 0

    @property
    def surfaces(self) -> tuple[BrowserSurface, ...]:
        return tuple(
            surface for surface in (self.root, self.popup) if surface is not None
        )

    @property
    def human_surface(self) -> BrowserSurface:
        surface = self.popup if self._selected_popup else self.root
        if surface is None or surface.viewer is None or surface.page.is_closed():
            raise BrowserInputError("browser_busy")
        return surface

    @property
    def human_busy(self) -> bool:
        return (
            self.human_io_pending
            or self._candidate_key is not None
            or bool(self._bootstrap)
            or self._popup_operations.pending
            or (
                self._popup_preparation is not None
                and not self._popup_preparation.done()
            )
            or any(
                surface.viewer is not None and surface.viewer.pending
                for surface in self.surfaces
            )
        )

    def _selection_changed(self) -> None:
        self.selection_revision += 1
        for surface in self.surfaces:
            surface.invalidate()

    def select(self, page_id: str) -> None:
        if page_id == self.page_id:
            self._selected_popup = False
        elif (
            self.popup is not None
            and not self.popup.page.is_closed()
            and page_id == self.popup.page_id
        ):
            self._selected_popup = True
        else:
            raise BrowserInputError("stale_input")
        self._selection_changed()

    def _extra_page(self, page: Page) -> None:
        if page is self.page or (self.popup is not None and page is self.popup.page):
            return
        key = frame_key(page.main_frame)
        provisional = self._bootstrap.pop(key, None)
        initial_url: str | None = None
        generation = self.popup_generation
        eligible = self._candidate_key is None
        if provisional is not None:
            initial_url, generation, arrived = provisional
            arrived.set()
            eligible = (
                self._candidate_key == key
                and initial_url is not None
                and generation is not None
                and generation == self.popup_generation
            )
            if self._candidate_key == key:
                self._candidate_key = None
        if (
            not eligible
            or not self.popup_enabled
            or self.popup is not None
            or self._popup_failed
            or self._popup_overflow
            or self._disposing
        ):
            self._reject_page(page)
            return
        surface = BrowserSurface(page)
        self.popup = surface
        self._popup_authorized = False
        self._popup_verified.clear()
        self._selected_popup = True
        self._selection_changed()
        page.on("close", lambda _page: self._popup_closed(surface))
        self._popup_preparation = asyncio.create_task(
            self._prepare_popup(surface, initial_url, generation)
        )
        self._popup_preparation.add_done_callback(_consume_popup_failure)

    async def _prepare_popup(
        self, surface: BrowserSurface, initial_url: str | None, generation: str | None
    ) -> None:
        try:
            async with asyncio.timeout(3):
                if (
                    await surface.page.opener() is not self.page
                    or not self.popup_enabled
                    or generation != self.popup_generation
                    or self._disposing
                ):
                    raise BrowserInputError("popup_not_allowed")
                await self._popup_operations.run(self._prepare_popup_surface(surface))
            if (
                not self.popup_enabled
                or generation != self.popup_generation
                or self._disposing
            ):
                raise BrowserInputError("popup_not_allowed")
            # The internal sandboxed document is never exposed as a ready view.
            if initial_url is not None:
                committed = surface.page.url
                if committed.split("#", 1)[0] != initial_url:
                    raise BrowserInputError("popup_not_allowed")
                self._popup_authorized = True
                self._popup_verified.set()
                async with asyncio.timeout(3):
                    await self._popup_operations.run(
                        self._navigate_popup(surface, committed)
                    )
            else:
                self._popup_authorized = True
                self._popup_verified.set()
                async with asyncio.timeout(3):
                    await self._popup_operations.run(
                        self._await_popup_navigation(surface)
                    )
            if (
                self.popup is not surface
                or not self.popup_enabled
                or generation != self.popup_generation
                or self._disposing
            ):
                raise BrowserInputError("popup_not_allowed")
            self.popup_notice = ""
            UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).info(
                "Browser sign-in page ready",
                data={
                    "event": "browser_popup_ready",
                    "browser_id": self.browser_id,
                    "page_id": surface.page_id,
                },
            )
        except Exception as error:
            if not surface.page.is_closed():
                self._popup_failed = True
                self._reject_page(surface.page)
            self._popup_verified.set()
            UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).warning(
                "Browser sign-in page preparation failed",
                data={
                    "event": "browser_popup_failed",
                    "browser_id": self.browser_id,
                    "error_type": type(error).__name__,
                },
            )

    async def _prepare_popup_surface(self, surface: BrowserSurface) -> None:
        await surface.page.set_viewport_size({"width": 1280, "height": 800})
        await surface.prepare(self.browser_id)

    async def _await_popup_navigation(self, surface: BrowserSurface) -> None:
        await surface.page.wait_for_url(
            lambda url: urlsplit(url).scheme in {"http", "https"},
            wait_until="commit",
            timeout=3000,
        )

    async def _navigate_popup(self, surface: BrowserSurface, url: str) -> None:
        await surface.page.goto(url, wait_until="commit", timeout=3000)

    async def _bootstrap_page(self, route: Route, key: str) -> None:
        # SDK Page events wait for the initial navigation to commit. Supply a
        # fixed, inert document so ownership can be verified BEFORE any website
        # request. Every further unverified request fails closed.
        if key in self._bootstrap or len(self._bootstrap) >= 4 or self._disposing:
            await self._fail_popup_context()
            return
        url = route.request.url
        eligible = (
            self.popup_enabled
            and self.popup is None
            and self._candidate_key is None
            and len(url) <= 8192
            and route.request.method == "GET"
            and route.request.post_data_buffer is None
            and not self._popup_failed
            and not self._popup_overflow
        )
        if not eligible:
            self.popup_notice = (
                "unsupported_navigation"
                if route.request.method != "GET" or len(url) > 8192
                else "popup_limit"
            )
        arrived = asyncio.Event()
        self._bootstrap[key] = (
            url if eligible else None,
            self.popup_generation,
            arrived,
        )
        if eligible:
            self._candidate_key = key
        job = asyncio.create_task(self._bootstrap_deadline(key, arrived))
        self._bootstrap_jobs.add(job)
        job.add_done_callback(self._bootstrap_jobs.discard)
        job.add_done_callback(_consume_popup_failure)
        UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).info(
            "Browser popup initialization reserved",
            data={
                "event": "browser_popup_bootstrap_reserved",
                "browser_id": self.browser_id,
                "eligible": eligible,
            },
        )
        try:
            async with asyncio.timeout(2):
                await self._popup_operations.run(
                    self._initialize_bootstrap(route, eligible)
                )
        except asyncio.CancelledError:
            self._popup_failed = True
            raise
        except Exception as error:
            self._popup_failed = True
            UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).warning(
                "Browser popup initialization failed",
                data={
                    "event": "browser_popup_initialization_failed",
                    "browser_id": self.browser_id,
                    "error_type": type(error).__name__,
                },
            )
            # Actual unfinished work stays owned; the runtime retires this
            # failed context. Do not leak SDK errors from route callbacks.

    async def _initialize_bootstrap(self, route: Route, eligible: bool) -> None:
        if eligible:
            await self.resolver.resolve(route.request.url)
        await route.fulfill(
            status=200,
            body="<!doctype html><title>Preparing sign-in</title>",
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Content-Security-Policy": "default-src 'none'; base-uri 'none'; form-action 'none'; sandbox allow-same-origin",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            },
        )

    async def _fail_popup_context(self) -> None:
        self._popup_failed = True
        try:
            async with asyncio.timeout(2):
                await self._popup_operations.run(self.context.close())
        except (Error, TimeoutError) as error:
            UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).warning(
                "Browser popup retirement is unconfirmed",
                data={
                    "event": "browser_popup_cleanup_unconfirmed",
                    "error_type": type(error).__name__,
                },
            )

    async def _bootstrap_deadline(self, key: str, arrived: asyncio.Event) -> None:
        try:
            async with asyncio.timeout(3):
                await arrived.wait()
        except TimeoutError:
            UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).warning(
                "Browser popup initialization deadline",
                data={
                    "event": "browser_popup_bootstrap_deadline",
                    "browser_id": self.browser_id,
                },
            )
            # A target that never becomes a Page must not strand capacity.
            self._popup_failed = True
            try:
                async with asyncio.timeout(2):
                    await self._popup_operations.run(self.context.close())
            finally:
                self._bootstrap.pop(key, None)
                if self._candidate_key == key:
                    self._candidate_key = None

    def _popup_closed(self, surface: BrowserSurface) -> None:
        if self.popup is surface:
            self._selected_popup = False
            self._selection_changed()

    def _reject_page(self, page: Page) -> None:
        if page in self._rejected_pages or self._popup_overflow:
            return
        self._rejected_pages.add(page)
        if len(self._popup_closures) >= 4:
            self._popup_overflow = True
            task = asyncio.create_task(self._close_rejected(self.context.close()))
        else:
            task = asyncio.create_task(self._close_rejected(page.close()))
        self._popup_closures.add(task)
        task.add_done_callback(self._popup_closures.discard)
        task.add_done_callback(_consume_popup_failure)

    async def _close_rejected(self, operation: Coroutine[Any, Any, None]) -> None:
        try:
            async with asyncio.timeout(2):
                await self._popup_operations.run(operation)
        except BaseException:
            self._popup_failed = True
            raise

    async def sync_popup(self) -> None:
        surface = self.popup
        if surface is None:
            return
        if self._popup_preparation is not None:
            async with asyncio.timeout(4):
                await asyncio.shield(self._popup_preparation)
        if self._popup_failed:
            raise BrowserInputError("browser_unavailable", retire=True)
        if surface.page.is_closed():
            try:
                async with asyncio.timeout(4):
                    await surface.dispose()
                if self._popup_operations.pending:
                    raise RuntimeError("Popup initialization has not settled")
            except BaseException:
                self._popup_failed = True
                raise
            # The slot remains reserved until actual adapter disposal completes.
            self.popup = None
            self._popup_preparation = None
            self._selected_popup = False
            self._selection_changed()

    async def close_popup(self) -> None:
        if self.popup is not None:
            try:
                async with asyncio.timeout(3):
                    await self._popup_operations.run(self.popup.page.close())
                await self.sync_popup()
            except BaseException:
                self._popup_failed = True
                raise

    async def read(self, url: str, timeout_ms: int) -> BrowserView:
        page = self.page
        if page is None or page.is_closed():
            raise BrowserNetworkError(
                "Browser page is closed; open a new interactive session"
            )
        observation = self.observation
        if observation is None:
            raise BrowserInteractionError("observation_failed")
        if url:
            await observation.invalidate()
            previous_document = await observation.document_identity()
            denied_before = self._denied_requests
            try:
                await self._navigations.run(
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                )
            except PlaywrightTimeoutError:
                UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).info(
                    "Browser navigation deadline recovery started",
                    data={
                        "event": "browser_navigation_deadline",
                        "status": "started",
                        "browser_id": self.browser_id,
                        "page_id": self.page_id,
                    },
                )
                await observation.stop_loading()
                committed_after_stop = await observation.document_identity()
                if self._denied_requests != denied_before:
                    raise BrowserInteractionError("destination_denied") from None
                retained = (
                    self.has_retained_document()
                    and not observation.broken
                    and committed_after_stop.web_document
                )
                raise BrowserReadDeadline(
                    self.browser_id,
                    self.page_id,
                    sanitize_url_for_log(page.url)[:2048],
                    page_retained=retained,
                    help_eligible=(
                        retained and committed_after_stop != previous_document
                    ),
                ) from None
            except asyncio.CancelledError as error:
                # Playwright's navigation deadline does not stop the remote
                # load. Abort it before releasing ownership, without replaying
                # a request or waiting for a document execution context.
                await self._stop_after_cancel(error)
                raise
            except Error:
                if self._denied_requests != denied_before:
                    raise BrowserInteractionError("destination_denied") from None
                raise
        content = await observation.read()
        self.revision += 1
        return BrowserView(
            self.browser_id,
            self.page_id,
            self.revision,
            sanitize_url_for_log(page.url)[:2048],
            content.text,
            content.observation_ref,
            content.elements,
            content.truncated,
        )

    async def act(
        self,
        operation: ActionOperation,
        reference: str,
        value: str,
        scroll_y: int,
        timeout_ms: int,
    ) -> BrowserView:
        page, observation = self.page, self.observation
        if page is None or observation is None:
            raise BrowserInteractionError("stale_reference")
        navigation = asyncio.Event()

        def navigated(frame: Frame) -> None:
            if frame is page.main_frame:
                navigation.set()

        page.on("framenavigated", navigated)
        dispatched = False
        try:
            # No write is retried, including when navigation/observation fails.
            navigating = await observation.act(operation, reference, value, scroll_y)
            dispatched = True
            if navigating or navigation.is_set():
                async with asyncio.timeout(timeout_ms / 1000):
                    await navigation.wait()
                    await page.wait_for_load_state(
                        "domcontentloaded", timeout=timeout_ms
                    )
            return await self.read("", timeout_ms)
        except BrowserInteractionError:
            if dispatched:
                raise BrowserInteractionError("action_uncertain") from None
            raise
        except (PlaywrightTimeoutError, TimeoutError):
            await observation.stop_loading()
            raise BrowserInteractionError("action_uncertain") from None
        except Error:
            raise BrowserInteractionError("action_uncertain") from None
        except asyncio.CancelledError as error:
            await self._stop_after_cancel(error)
            raise
        finally:
            page.remove_listener("framenavigated", navigated)

    async def _stop_after_cancel(self, error: asyncio.CancelledError) -> None:
        observation = self.observation
        if observation is None or observation.broken:
            return
        try:
            await observation.stop_loading()
        except asyncio.CancelledError:
            error.add_note("Navigation stop interrupted by repeated cancellation")
        except BaseException as cleanup_error:
            error.add_note(f"Navigation stop failed: {type(cleanup_error).__name__}")

    async def dispose(self) -> None:
        self.popup_enabled = False
        self._disposing = True
        try:
            for _, _, arrived in self._bootstrap.values():
                arrived.set()
            if self._bootstrap_jobs:
                async with asyncio.timeout(4):
                    await asyncio.gather(
                        *(asyncio.shield(task) for task in self._bootstrap_jobs)
                    )
            if self._popup_preparation is not None:
                async with asyncio.timeout(7):
                    await asyncio.shield(self._popup_preparation)
        finally:
            try:
                if self.popup is not None:
                    await self.popup.dispose()
            finally:
                if self.root is not None:
                    await self.root.dispose()
        if self._popup_closures:
            async with asyncio.timeout(3):
                await asyncio.gather(
                    *(asyncio.shield(task) for task in self._popup_closures)
                )
        if not await self._popup_operations.settled():
            raise RuntimeError("Popup operations have not settled")

    def needs_disposal(self) -> bool:
        return self._popup_failed or (
            self.observation is not None and self.observation.broken
        )

    def has_retained_document(self) -> bool:
        return (
            self.is_usable()
            and self.page is not None
            and urlsplit(self.page.url).scheme in {"http", "https"}
        )

    def is_usable(self) -> bool:
        return (
            not self._popup_failed
            and not self._popup_overflow
            and self.page is not None
            and not self.page.is_closed()
            and self.context.browser is not None
            and self.context.browser.is_connected()
        )

    async def _route(self, route: Route) -> None:
        try:
            key = early_frame_key(route.request)
            if key is not None:
                await self._bootstrap_page(route, key)
                return
            page = route.request.frame.page
            if page is not self.page:
                self._extra_page(page)
                if (
                    self.popup is not None
                    and page is self.popup.page
                    and not self._popup_authorized
                ):
                    main_navigation = (
                        route.request.is_navigation_request()
                        and frame_key(route.request.frame) == frame_key(page.main_frame)
                    )
                    if (
                        not main_navigation
                        or self._verification_waiter is not None
                        or self._popup_failed
                        or self._disposing
                    ):
                        if main_navigation and self._verification_waiter is not None:
                            self._popup_failed = True
                        await route.abort("blockedbyclient")
                        return
                    # Only one main-document request may wait for verification.
                    # Resource requests never acquire an unbounded wait queue.
                    owner = object()
                    self._verification_waiter = owner
                    try:
                        async with asyncio.timeout(3):
                            await self._popup_verified.wait()
                    finally:
                        if self._verification_waiter is owner:
                            self._verification_waiter = None
                if (
                    self.popup is None
                    or page is not self.popup.page
                    or not self._popup_authorized
                    or self._popup_failed
                    or page.is_closed()
                ):
                    await route.abort("blockedbyclient")
                    return
            await self.resolver.resolve(route.request.url)
        except WebUrlPolicyError:
            if key is not None:
                await self._fail_popup_context()
            self._denied_requests += 1
            await route.abort("blockedbyclient")
            return
        except (BrowserNetworkError, Error, TimeoutError):
            await route.abort("blockedbyclient")
            return
        await route.continue_()


async def block_websocket(route: WebSocketRoute) -> None:
    await route.close(code=1008, reason="Browser WebSockets are not supported")


def _consume_popup_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()
