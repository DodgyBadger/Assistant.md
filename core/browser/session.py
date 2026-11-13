"""A retained chat-owned page; browser engine objects stay on their runtime loop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from playwright.async_api import (
    BrowserContext,
    Error,
    Frame,
    Page,
    Route,
    WebSocketRoute,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from core.web.errors import WebUrlPolicyError
from core.web.security import sanitize_url_for_log

from .network import BrowserNetworkError, BrowserResolver
from .observation import (
    ActionOperation,
    BrowserElement,
    BrowserInteractionError,
    PageObservation,
)


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
    page: Page | None = None
    revision: int = 0
    observation: PageObservation | None = None
    _popup_closures: set[asyncio.Task[None]] = field(default_factory=set)
    _popup_overflow: bool = False

    async def prepare(self) -> None:
        await self.context.route("**/*", self._route)
        await self.context.route_web_socket("**/*", block_websocket)
        self.page = await self.context.new_page()
        self.page.set_default_timeout(5000)
        connection = await self.context.new_cdp_session(self.page)
        self.observation = PageObservation(connection, self.browser_id, self.page_id)
        self.context.on("page", self._extra_page)

    def _extra_page(self, page: Page) -> None:
        if page is self.page or self._popup_overflow:
            return
        if len(self._popup_closures) >= 4:
            self._popup_overflow = True
            task = asyncio.create_task(self.context.close())
        else:
            task = asyncio.create_task(page.close())
        self._popup_closures.add(task)
        task.add_done_callback(self._popup_closures.discard)
        task.add_done_callback(_consume_popup_failure)

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
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except (PlaywrightTimeoutError, asyncio.CancelledError) as error:
                # Playwright's navigation deadline does not stop the remote
                # load. Abort it before releasing ownership, without replaying
                # a request or waiting for a document execution context.
                try:
                    await observation.stop_loading()
                except asyncio.CancelledError:
                    if not isinstance(error, asyncio.CancelledError):
                        raise
                    error.add_note(
                        "Navigation stop interrupted by repeated cancellation"
                    )
                except BaseException as cleanup_error:
                    error.add_note(
                        f"Navigation stop failed: {type(cleanup_error).__name__}"
                    )
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
        except (Error, TimeoutError):
            raise BrowserInteractionError("action_uncertain") from None
        finally:
            page.remove_listener("framenavigated", navigated)

    async def dispose(self) -> None:
        if self.observation is not None:
            await self.observation.close()

    def needs_disposal(self) -> bool:
        return self.observation is not None and self.observation.broken

    def is_usable(self) -> bool:
        return (
            self.page is not None
            and not self.page.is_closed()
            and self.context.browser is not None
            and self.context.browser.is_connected()
        )

    async def _route(self, route: Route) -> None:
        try:
            # Until authentication-popup support is implemented, no additional
            # page gets to start requests. The proxy still guards every socket,
            # including redirects that Playwright does not route a second time.
            if route.request.frame.page is not self.page:
                await route.abort("blockedbyclient")
                return
            await self.resolver.resolve(route.request.url)
        except (WebUrlPolicyError, BrowserNetworkError, Error):
            await route.abort("blockedbyclient")
            return
        await route.continue_()


async def block_websocket(route: WebSocketRoute) -> None:
    await route.close(code=1008, reason="Browser WebSockets are not supported")


def _consume_popup_failure(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()
