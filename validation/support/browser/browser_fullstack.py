"""Real app setup and user navigation shared across full-stack browser scenarios."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

from playwright.async_api import expect


async def open_explorer(ui, application):
    if ui.url.rstrip("/") != application.url:
        await ui.goto(application.url)
    selector = ui.locator("#vault-selector")
    if await selector.input_value() != application.vault.name:
        await selector.select_option(application.vault.name)
    await ui.get_by_role("button", name="Open vault explorer", exact=True).click()
    explorer = ui.get_by_role("dialog", name="Vault Explorer", exact=True)
    await expect(explorer).to_be_visible()
    return explorer


async def read_file(ui, application, path):
    explorer = await open_explorer(ui, application)
    await explorer.get_by_role("button", name="Expand all folders").click()
    # File paths are stable app identities, not visual structure or CSS classes.
    await explorer.locator(f'[data-vault-path-picker-select="{path}"]').click()
    reader = ui.get_by_role("dialog").filter(has=ui.locator("#vault-file-modal-path"))
    await expect(reader.locator("#vault-file-modal-path")).to_have_text(path)
    await expect(reader.locator("#vault-file-modal-preview")).to_be_visible()
    return reader


async def select_passage(ui, reader, text, instruction=""):
    paragraph = (
        reader.locator("#vault-file-modal-preview p").filter(has_text=text).first
    )
    # The DOM Range is browser selection input, not a replacement app callback.
    # User-facing selection/capture code handles the resulting selection event.
    await paragraph.evaluate("""element => {
        const range = document.createRange(); range.selectNodeContents(element);
        const selection = window.getSelection(); selection.removeAllRanges();
        selection.addRange(range);
        document.dispatchEvent(new Event('selectionchange'));
    }""")
    await ui.get_by_role("button", name="Ask about selected text", exact=True).click()
    await ui.get_by_label("Instruction (optional)").fill(instruction)


def setting(app, key, value):
    response = app.request(
        f"/api/system/settings/general/{key}", method="PUT", data={"value": str(value)}
    )
    assert response.status_code == 200, response.text
    return response.json()


def deterministic_chat(application):
    app = application.app
    setting(app, "default_model", "test")
    metadata = app.request("/api/metadata").json()
    setting(
        app, "disabled_tools", json.dumps([row["name"] for row in metadata["tools"]])
    )


@dataclass
class DeliveryGate:
    method: str = "GET"
    held: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    requests: list = field(default_factory=list)


@asynccontextmanager
async def _scoped_route(ui, pattern, handler):
    # Keep interception on the context's existing catch-all admission route.
    # Removing the last page route can auto-continue a queued request before
    # its Python callback reaches admission, producing a double-handled route.
    active = True
    pending = set()

    async def dispatch(route):
        task = asyncio.current_task()
        pending.add(task)
        try:
            if active:
                await handler(route)
            else:
                await route.fallback()
        finally:
            pending.discard(task)

    await ui.context.route(pattern, dispatch)
    try:
        yield
    finally:
        active = False
        # Remove only this scope; concurrent and nested gates retain ownership.
        await ui.context.unroute(pattern, dispatch)
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending), 15)


@asynccontextmanager
async def hold_delivery(ui, app, pattern, *, method="GET", first_only=False):
    gate = DeliveryGate(method=method)
    claimed = False

    async def hold(route):
        nonlocal claimed
        if route.request.method != method:
            await route.fallback()
            return
        if first_only and claimed:
            await route.fallback()
            return
        claimed = True
        # Only delivery is delayed. The real app executes and its original
        # status, headers and body are forwarded intact. No app service or API
        # result is mocked. This is an environmental network interleaving.
        response = await route.fetch()
        row = {"url": route.request.url, "status": response.status}
        gate.requests.append(row)
        gate.held.set()
        await gate.release.wait()
        await route.fulfill(response=response)
        row["delivery"] = "forwarded_unchanged"

    try:
        async with _scoped_route(ui, pattern, hold):
            try:
                yield gate
            finally:
                gate.release.set()
    finally:
        app.evidence.append(
            "network-delivery-gates.jsonl",
            {
                "pattern": pattern,
                "method": method,
                "requests": gate.requests,
                "scope": "delivery only; real app responses retained",
            },
        )


@asynccontextmanager
async def lose_connection(ui, app, pattern, *, method="GET"):
    calls = []

    async def abort(route):
        if route.request.method != method:
            await route.fallback()
            return
        # A transport drop, not a manufactured application error response.
        # No app service, response body or semantic observation is substituted.
        calls.append(route.request.url)
        await route.abort("connectionfailed")

    try:
        async with _scoped_route(ui, pattern, abort):
            yield calls
    finally:
        app.evidence.append(
            "network-drops.jsonl",
            {"pattern": pattern, "method": method, "calls": calls},
        )


@asynccontextmanager
async def hold_deliveries(ui, app, *specifications):
    """Coordinate real responses with independent, scoped route ownership."""
    gates = []
    try:
        async with AsyncExitStack() as stack:
            try:
                for pattern, method in specifications:
                    gate = DeliveryGate(method=method)
                    gates.append(gate)

                    async def hold(route, *, current=gate):
                        if route.request.method != current.method:
                            await route.fallback()
                            return
                        # Environmental timing only: retain the real app response intact.
                        response = await route.fetch()
                        row = {"url": route.request.url, "status": response.status}
                        current.requests.append(row)
                        current.held.set()
                        await current.release.wait()
                        await route.fulfill(response=response)
                        row["delivery"] = "forwarded_unchanged"

                    await stack.enter_async_context(_scoped_route(ui, pattern, hold))
                yield gates
            finally:
                for gate in gates:
                    gate.release.set()
    finally:
        app.evidence.save_json(
            "coordinated-response-delivery.json", [g.requests for g in gates]
        )
