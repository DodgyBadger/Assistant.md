"""Real app setup and user navigation shared across full-stack browser scenarios."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
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
async def hold_delivery(ui, app, pattern, *, method="GET"):
    gate = DeliveryGate(method=method)

    async def hold(route):
        if route.request.method != method:
            await route.fallback()
            return
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

    await ui.route(pattern, hold)
    try:
        yield gate
    finally:
        gate.release.set()
        # The public wait policy drains SDK-tracked invocations, including ones
        # queued before our callback starts. Tracking only callback tasks misses
        # that interval and can cause a late route to be handled twice.
        await asyncio.wait_for(ui.unroute_all(behavior="wait"), 15)
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

    await ui.route(pattern, abort)
    try:
        yield calls
    finally:
        await ui.unroute(pattern, abort)
        app.evidence.append(
            "network-drops.jsonl",
            {"pattern": pattern, "method": method, "calls": calls},
        )
