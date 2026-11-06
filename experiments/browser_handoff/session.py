"""Single temporary browser session with separate human and agent operations."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import HTTPException
from playwright.async_api import (
    Browser,
    BrowserContext,
    CDPSession,
    ElementHandle,
    Page,
    Route,
)
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from pydantic import BaseModel, ConfigDict, Field

from experiments.browser_handoff.probe import public_host

Owner = Literal["human", "agent", "closed"]


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)


class Control(Revision):
    owner: Literal["human", "agent"]


class Navigate(Revision):
    url: str = Field(max_length=4096)


class HumanInput(Revision):
    kind: Literal["click", "text", "key", "scroll"]
    x: float = Field(default=0, ge=0, lt=1280, allow_inf_nan=False)
    y: float = Field(default=0, ge=0, lt=800, allow_inf_nan=False)
    text: str = Field(default="", max_length=4096)
    key: Literal[
        "Enter",
        "Tab",
        "Shift+Tab",
        "Escape",
        "Backspace",
        "Delete",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "Home",
        "End",
        "PageUp",
        "PageDown",
        "ControlOrMeta+A",
    ] = "Enter"
    delta_x: float = Field(default=0, ge=-2000, le=2000, allow_inf_nan=False)
    delta_y: float = Field(default=0, ge=-2000, le=2000, allow_inf_nan=False)


class AgentCommand(Revision):
    operation: Literal["read", "navigate", "click", "fill", "select"]
    element_id: int = Field(default=0, ge=0)
    value: str = Field(default="", max_length=4096)
    url: str = Field(default="", max_length=4096)


class Element(BaseModel):
    id: int
    tag: str
    label: str


class View(BaseModel):
    revision: int
    url: str
    text: str
    elements: list[Element]


class State(BaseModel):
    session_id: str
    revision: int
    owner: Owner
    url: str
    page_closed: bool


class BrowserSession:
    """Cooperative API authority; unrestricted container code is outside this boundary."""

    def __init__(
        self,
        context: BrowserContext,
        page: Page,
        demo_origin: str | None,
        preview: CDPSession,
    ) -> None:
        self.context, self.page, self.demo_origin = context, page, demo_origin
        self.preview = preview
        self.owner: Owner = "agent"
        self.session_id = uuid4().hex
        self.revision = 0
        self.lock = asyncio.Lock()
        self.preview_lock = asyncio.Lock()
        self.elements: dict[int, ElementHandle] = {}
        self.elements_revision = -1
        self.published = self.state()

    @classmethod
    async def create(cls, browser: Browser, demo_origin: str | None) -> BrowserSession:
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            accept_downloads=False,
            service_workers="block",
        )
        page = await context.new_page()
        page.set_default_timeout(5000)
        session = cls(context, page, demo_origin, await context.new_cdp_session(page))
        await context.route("**/*", session._route)

        async def close_popup(popup: Page) -> None:
            if popup != page:
                await popup.close()

        context.on("page", close_popup)
        return session

    def state(self) -> State:
        parsed = urlsplit(self.page.url)
        safe_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return State(
            session_id=self.session_id,
            revision=self.revision,
            owner=self.owner,
            url=safe_url,
            page_closed=self.page.is_closed(),
        )

    async def status(self) -> State:
        # State observation must not wait for page rendering or browser commands.
        return self.published.model_copy(
            update={"url": self.state().url, "page_closed": self.page.is_closed()}
        )

    @asynccontextmanager
    async def operation(self) -> AsyncIterator[None]:
        async with self.lock:
            try:
                async with asyncio.timeout(12):
                    yield
            except TimeoutError as exc:
                self.revision += 1
                self.elements_revision = -1
                raise HTTPException(504, "browser_operation_timed_out") from exc
            finally:
                self.published = self.state()

    def _require(self, revision: int | None = None, owner: Owner | None = None) -> None:
        if self.owner == "closed" or self.page.is_closed():
            raise HTTPException(410, "session_closed")
        if owner is not None and self.owner != owner:
            raise HTTPException(
                409, "human_in_control" if self.owner == "human" else "agent_in_control"
            )
        if revision is not None and revision != self.revision:
            raise HTTPException(409, "stale_view")

    async def _invalidate(self) -> None:
        self.revision += 1
        self.elements_revision = -1
        previous, self.elements = self.elements, {}
        for handle in previous.values():
            await handle.dispose()

    async def control(self, command: Control) -> State:
        async with self.operation():
            # A takeover supersedes the agent's latest view. It must remain
            # available while the agent is actively reading or acting.
            self._require(None if command.owner == "human" else command.revision)
            if command.owner == "agent":
                for frame in self.page.frames:
                    populated = await frame.evaluate(
                        """() => Array.from(document.querySelectorAll('input[type="password"],input[autocomplete="current-password"],input[autocomplete="new-password"],input[autocomplete="one-time-code"]')).some(e => e.value.length > 0)"""
                    )
                    if populated:
                        raise HTTPException(409, "credential_field_still_populated")
            await self._invalidate()
            self.owner = command.owner
            return self.state()

    async def navigate(self, command: Navigate) -> State:
        async with self.operation():
            self._require(command.revision, "human")
            await self._check_url(command.url)
            await self._invalidate()
            await self.page.goto(
                command.url, wait_until="domcontentloaded", timeout=25000
            )
            return self.state()

    async def input(self, command: HumanInput) -> State:
        async with self.operation():
            self._require(command.revision, "human")
            if command.kind == "click":
                await self.page.mouse.click(command.x, command.y)
            elif command.kind == "text":
                await self.page.keyboard.insert_text(command.text)
            elif command.kind == "key":
                await self.page.keyboard.press(command.key)
            else:
                await self.page.mouse.wheel(command.delta_x, command.delta_y)
            return self.state()

    async def frame(self) -> bytes:
        async with self.preview_lock:
            self._require()
            try:
                async with asyncio.timeout(3):
                    # A live view shows the current compositor frame; it must not
                    # wait for web fonts or prepare a publication screenshot.
                    result = await self.preview.send(
                        "Page.captureScreenshot", {"format": "jpeg", "quality": 65}
                    )
                    return base64.b64decode(result["data"], validate=True)
            except (TimeoutError, PlaywrightTimeoutError) as exc:
                raise HTTPException(504, "browser_frame_timed_out") from exc

    async def execute(self, command: AgentCommand) -> View:
        async with self.operation():
            self._require(command.revision, "agent")
            if command.operation == "navigate":
                await self._check_url(command.url)
                await self._invalidate()
                await self.page.goto(
                    command.url, wait_until="domcontentloaded", timeout=25000
                )
            elif command.operation != "read":
                handle = self.elements.get(command.element_id)
                if (
                    self.elements_revision != command.revision
                    or handle is None
                    or not await handle.evaluate("e => e.isConnected")
                ):
                    raise HTTPException(409, "stale_element")
                credential = await handle.evaluate(
                    """e => e.matches('input[type="password"],input[autocomplete="current-password"],input[autocomplete="new-password"],input[autocomplete="one-time-code"]')"""
                )
                if credential:
                    raise HTTPException(409, "human_credential_entry_required")
                # Consume this view before transmission: a timeout cannot make an
                # identical action eligible for an automatic duplicate replay.
                self.revision += 1
                try:
                    if command.operation == "click":
                        await handle.click()
                        await self.page.wait_for_load_state("domcontentloaded")
                    elif command.operation == "fill":
                        await handle.fill(command.value)
                    else:
                        await handle.select_option(command.value)
                finally:
                    await self._invalidate()
            return await self._snapshot()

    async def open_for_agent(self, url: str) -> None:
        """Open a page without requiring extraction before human takeover."""
        async with self.operation():
            self._require(owner="agent")
            await self._check_url(url)
            await self._invalidate()
            await self.page.goto(url, wait_until="domcontentloaded", timeout=10000)

    async def _snapshot(self) -> View:
        await self._invalidate()
        selected = await self.page.evaluate_handle(
            """() => Array.from(document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,[role=button]')).filter(e => !e.matches('input[type="password"],input[autocomplete="current-password"],input[autocomplete="new-password"],input[autocomplete="one-time-code"]') && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden').slice(0,80)"""
        )
        table: list[Element] = []
        try:
            descriptions = await selected.evaluate(
                """elements => elements.map(e => ({tag: e.tagName.toLowerCase(), label: (e.getAttribute('aria-label') || (e.labels && Array.from(e.labels).map(l => l.innerText).join(' ')) || e.innerText || e.getAttribute('name') || e.tagName).trim().slice(0,160)}))"""
            )
            handles = await selected.get_properties()
            for key, handle in handles.items():
                element = handle.as_element()
                if key.isdecimal() and element is not None:
                    index = int(key) + 1
                    description = descriptions[int(key)]
                    self.elements[index] = element
                    table.append(
                        Element(
                            id=index, tag=description["tag"], label=description["label"]
                        )
                    )
                else:
                    await handle.dispose()
        finally:
            await selected.dispose()
        text = await self.page.locator("body").inner_text(timeout=5000)
        self.elements_revision = self.revision
        return View(
            revision=self.revision,
            url=self.state().url,
            text=text[:12000],
            elements=table,
        )

    async def close(self) -> State:
        async with self.operation():
            if self.owner != "closed":
                await self._invalidate()
                await self.context.close()
                self.owner = "closed"
            return self.state()

    async def _allowed(self, url: str) -> bool:
        if self.demo_origin is not None and url.startswith(self.demo_origin + "/"):
            return True
        return await asyncio.to_thread(public_host, url)

    async def _check_url(self, url: str) -> None:
        if not await self._allowed(url):
            raise HTTPException(400, "url_not_allowed")

    async def _route(self, route: Route) -> None:
        if await self._allowed(route.request.url):
            await route.continue_()
        else:
            await route.abort()
