"""Real app HTTP ingress and diagnostic evidence for browser-owned user journeys."""

from __future__ import annotations

import asyncio
import os
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
from playwright.async_api import Browser, Error, Page

from validation.scenarios.application import Application


def descendant_identities() -> list[dict]:
    """Record identities, including detached descendants, for post-run cleanup proof."""
    records = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            records[int(entry.name)] = {
                "pid": int(entry.name),
                "parent": int(fields[1]),
                "start_time": fields[19],
                "state": fields[0],
            }
        except FileNotFoundError:
            continue
    owned = {os.getpid()}
    while (
        additions := {pid for pid, row in records.items() if row["parent"] in owned}
        - owned
    ):
        owned.update(additions)
    return [records[pid] for pid in sorted(owned - {os.getpid()})]


@dataclass
class BrowserApplication:
    app: Application
    url: str
    vault: Path


@asynccontextmanager
async def app_http_listener(app: Application):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    sock.setblocking(False)
    port = sock.getsockname()[1]
    # Runtime startup belongs to the app fixture. Uvicorn only exposes that
    # same production app/routes/assets on a socket; it must not run lifespan
    # startup again or create a second runtime/event loop.
    server = uvicorn.Server(
        uvicorn.Config(
            app.controller.get_api_test_client().app,
            lifespan="off",
            log_level="error",
            timeout_graceful_shutdown=3,
        )
    )
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if serving.done():
                    await serving
                    raise RuntimeError("App HTTP listener stopped before readiness")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(serving, 8)
        finally:
            sock.close()
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except ConnectionRefusedError:
            app.evidence.save_json(
                "http-listener-cleanup.json", {"port": port, "closed": True}
            )
        else:
            writer.close()
            await writer.wait_closed()
            raise AssertionError("App HTTP listener remained reachable after shutdown")


@asynccontextmanager
async def observed_page(application: BrowserApplication, page: Page, browser: Browser):
    evidence = application.app.evidence
    console, errors, failures, responses, denied = [], [], [], [], []
    origin = urlsplit(application.url)

    async def admit(route):
        target = urlsplit(route.request.url)
        if (target.scheme, target.hostname, target.port) == (
            origin.scheme,
            origin.hostname,
            origin.port,
        ):
            await route.continue_()
        else:
            denied.append(route.request.url)
            await route.abort("blockedbyclient")

    async def reject_websocket(route):
        denied.append(route.url)
        await route.close(code=1008, reason="Undeclared browser WebSocket")

    page.on(
        "console",
        lambda message: console.append({"level": message.type, "text": message.text}),
    )
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "requestfailed",
        lambda request: failures.append(
            {"url": request.url, "failure": request.failure}
        ),
    )
    page.on(
        "response",
        lambda response: responses.append(
            {"url": response.url, "status": response.status}
        ),
    )
    context = page.context
    closed = False
    await context.tracing.start(screenshots=True, snapshots=True, sources=False)
    try:
        # A Python socket audit cannot constrain Chromium's native networking.
        # These are browser-profile request controls, not an OS sandbox. No app
        # responses are mocked: only the actual local app origin is admitted.
        await context.route("**/*", admit)
        await context.route_web_socket("**/*", reject_websocket)
        yield page
    finally:
        evidence.save_json("browser-process-identities.json", descendant_identities())
        export_errors = []
        try:
            try:
                if not page.is_closed():
                    await page.screenshot(
                        path=evidence.artifacts / "browser.png",
                        full_page=True,
                        timeout=5000,
                    )
            except Error as error:
                export_errors.append({"artifact": "screenshot", "error": str(error)})
            try:
                await context.tracing.stop(
                    path=evidence.artifacts / "browser-trace.zip"
                )
            except Error as error:
                export_errors.append({"artifact": "trace", "error": str(error)})
        finally:
            try:
                await context.close()
                closed = context not in browser.contexts
            finally:
                evidence.save_json(
                    "browser-diagnostics.json",
                    {
                        "console": console,
                        "page_errors": errors,
                        "request_failures": failures,
                        "responses": responses,
                        "denied_requests": denied,
                        "context_closed": closed,
                        "export_errors": export_errors,
                        "network_scope": "local app origin; service workers blocked; no OS sandbox",
                    },
                )
        assert closed, "Browser context cleanup is unconfirmed"
        assert (
            not export_errors
        ), "Browser diagnostic export failed; see browser-diagnostics.json"
