"""Disposable chat integration. Enabled only by ASSISTANTMD_INTERACTIVE_BROWSER=1."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from playwright.async_api import Browser, async_playwright

from api.principal import use_request_authority
from core.identity import require_current_execution_authority
from core.runtime.execution_tasks import get_current_execution_task
from experiments.browser_handoff.console import ASSETS
from experiments.browser_handoff.probe import launch
from experiments.browser_handoff.runtime import browser_runtime
from experiments.browser_handoff.session import (
    AgentCommand,
    BrowserSession,
    Control,
    HumanInput,
    Navigate,
    State,
)


@dataclass
class Entry:
    id: str
    principal_id: str
    session: BrowserSession
    returned: asyncio.Event = field(default_factory=asyncio.Event)
    waiting: bool = False

    @property
    def link(self) -> str:
        return f"/browser/{self.id}/?takeover=1"


class InteractiveBrowsers:
    def __init__(self) -> None:
        self.stack = AsyncExitStack()
        self.browser: Browser | None = None
        self.entries: dict[str, Entry] = {}
        self.scopes: dict[tuple[str, str], str] = {}
        self.lock = asyncio.Lock()

    def scope(self) -> tuple[str, str]:
        task = get_current_execution_task()
        if task is None or not task.scope.startswith("chat_session:"):
            raise RuntimeError("Interactive browser prototype requires a chat session.")
        return task.principal_id, task.scope

    def active(self) -> bool:
        task = get_current_execution_task()
        return bool(task and (task.principal_id, task.scope) in self.scopes)

    async def get(self) -> Entry:
        key = self.scope()
        async with self.lock:
            entry_id = self.scopes.get(key)
            if entry_id is not None:
                entry = self.entries[entry_id]
                if not entry.session.page.is_closed():
                    return entry
            if self.browser is None:
                try:
                    self.stack.enter_context(browser_runtime("headful_cdp"))
                    playwright = await self.stack.enter_async_context(
                        async_playwright()
                    )
                    self.browser = await self.stack.enter_async_context(
                        launch(playwright, "headful_cdp")
                    )
                except BaseException:
                    await self.stack.aclose()
                    raise
            session = await BrowserSession.create(self.browser, None)
            self.stack.push_async_callback(session.close)
            entry = Entry(uuid4().hex, key[0], session)
            self.entries[entry.id] = entry
            self.scopes[key] = entry.id
            return entry

    def lookup(self, entry_id: str) -> Entry:
        entry = self.entries.get(entry_id)
        authority = require_current_execution_authority()
        if entry is None or entry.principal_id != authority.principal_id:
            raise HTTPException(404, "browser_not_found")
        return entry

    async def close(self) -> None:
        for entry in self.entries.values():
            entry.returned.set()
        await self.stack.aclose()
        self.browser = None
        self.entries.clear()
        self.scopes.clear()


browsers = InteractiveBrowsers()


async def publish(entry: Entry, status: str, reason: str) -> None:
    # Deliberately reuse the current chat turn, rather than enqueue a new prompt.
    from core.chat.task_execution import CHAT_TASK_EVENT_BUFFER

    task = get_current_execution_task()
    if task is not None and not await CHAT_TASK_EVENT_BUFFER.is_terminal(task.task_id):
        await CHAT_TASK_EVENT_BUFFER.append(
            task.task_id,
            "browser_handoff",
            {
                "event": "browser_handoff",
                "url": entry.link,
                "status": status,
                "reason": reason,
            },
        )


async def browse(
    *,
    url: str,
    operation: Literal["read", "click", "fill", "select"],
    element_id: int,
    value: str,
    request_human: bool,
    reason: str,
) -> str:
    entry = await browsers.get()
    session = entry.session
    if session.owner != "agent" and not request_human:
        raise RuntimeError(
            "Human currently controls this browser. Request human help to wait for handback."
        )
    if session.owner == "agent" and url and url != session.page.url:
        await session.execute(
            AgentCommand(operation="navigate", revision=session.revision, url=url)
        )
    if request_human:
        entry.returned.clear()
        entry.waiting = True
        await publish(
            entry,
            "waiting",
            reason or "Please complete the browser step, then return control.",
        )
        completed = False
        try:
            async with asyncio.timeout(600):
                await entry.returned.wait()
            if session.owner != "agent" or session.page.is_closed():
                raise RuntimeError("Browser was closed during human takeover.")
            completed = True
        finally:
            entry.waiting = False
            await publish(entry, "returned" if completed else "stopped", "")
    command = AgentCommand(
        operation="read" if request_human else operation,
        revision=session.revision,
        element_id=element_id,
        value=value,
    )
    view = await session.execute(command)
    return (
        view.model_dump_json()
        + "\nInteractive browser retained for this chat. Use element IDs for click/fill/select; call request_human=true if a human step is needed. Never request or enter the user's passwords."
    )


def router() -> APIRouter:
    routes = APIRouter(prefix="/browser", dependencies=[Depends(use_request_authority)])

    @routes.get("/{entry_id}/")
    async def index(entry_id: str) -> FileResponse:
        browsers.lookup(entry_id)
        return FileResponse(
            ASSETS / "index.html", headers={"Cache-Control": "no-store"}
        )

    @routes.get("/{entry_id}/console.js")
    async def javascript(entry_id: str) -> FileResponse:
        browsers.lookup(entry_id)
        return FileResponse(ASSETS / "console.js", media_type="text/javascript")

    @routes.get("/{entry_id}/console.css")
    async def stylesheet(entry_id: str) -> FileResponse:
        browsers.lookup(entry_id)
        return FileResponse(ASSETS / "console.css", media_type="text/css")

    @routes.get("/{entry_id}/human/state")
    async def state(entry_id: str) -> State:
        return await browsers.lookup(entry_id).session.status()

    @routes.get("/{entry_id}/human/frame")
    async def frame(entry_id: str) -> Response:
        return Response(
            await browsers.lookup(entry_id).session.frame(),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @routes.post("/{entry_id}/human/control")
    async def control(entry_id: str, command: Control) -> State:
        entry = browsers.lookup(entry_id)
        previous = entry.session.owner
        result = await entry.session.control(command)
        if previous == "human" and command.owner == "agent" and entry.waiting:
            entry.returned.set()
        return result

    @routes.post("/{entry_id}/human/input")
    async def browser_input(entry_id: str, command: HumanInput) -> State:
        return await browsers.lookup(entry_id).session.input(command)

    @routes.post("/{entry_id}/human/navigate")
    async def navigate(entry_id: str, command: Navigate) -> State:
        return await browsers.lookup(entry_id).session.navigate(command)

    @routes.post("/{entry_id}/human/close")
    async def close(entry_id: str) -> State:
        entry = browsers.lookup(entry_id)
        result = await entry.session.close()
        entry.returned.set()
        return result

    return routes
