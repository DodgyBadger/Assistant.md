"""Standalone in-container browser takeover experiment."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from starlette.middleware.base import RequestResponseEndpoint
from starlette.middleware.trustedhost import TrustedHostMiddleware

from experiments.browser_handoff.fixture import login_site
from experiments.browser_handoff.probe import (
    MODES,
    BrowserInstallError,
    chromium_executable,
    launch,
)
from experiments.browser_handoff.runtime import browser_runtime
from experiments.browser_handoff.session import (
    AgentCommand,
    BrowserSession,
    Control,
    HumanInput,
    Navigate,
    State,
    View,
)

ASSETS = Path(__file__).parent / "console"


@dataclass(frozen=True)
class Options:
    human_token: str = field(repr=False)
    agent_token: str = field(repr=False)
    mode: str = "headful_cdp"
    demo: bool = True


def create_app(options: Options) -> FastAPI:
    session: BrowserSession | None = None
    demo: tuple[str, str] | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal session, demo
        async with AsyncExitStack() as stack:
            stack.enter_context(browser_runtime(options.mode))
            demo = stack.enter_context(login_site()) if options.demo else None
            playwright = await stack.enter_async_context(async_playwright())
            browser = await stack.enter_async_context(launch(playwright, options.mode))
            session = await BrowserSession.create(browser, demo[0] if demo else None)
            stack.push_async_callback(session.close)
            yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]
    )

    def browser_session() -> BrowserSession:
        if session is None:
            raise HTTPException(503, "browser_not_ready")
        return session

    def authenticate(
        request: Request, token: str, *, human_header: bool = False
    ) -> None:
        supplied = (
            request.headers.get("X-Browser-Handoff-Token", "") if human_header else ""
        )
        expected = token
        if not supplied:
            supplied = request.headers.get("Authorization", "")
            expected = "Bearer " + token
        if not secrets.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(401, "unauthorized")

    def human(request: Request) -> None:
        authenticate(request, options.human_token, human_header=True)

    def agent(request: Request) -> None:
        authenticate(request, options.agent_token)

    @app.middleware("http")
    async def private_responses(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Pydantic's default error payload can echo human input or credentials.
        return JSONResponse({"detail": "invalid_request"}, status_code=400)

    @app.exception_handler(PlaywrightError)
    async def browser_error(request: Request, exc: PlaywrightError) -> JSONResponse:
        return JSONResponse({"detail": "browser_operation_failed"}, status_code=409)

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(ASSETS / "index.html")

    @app.get("/console.js")
    async def javascript() -> FileResponse:
        return FileResponse(ASSETS / "console.js", media_type="text/javascript")

    @app.get("/console.css")
    async def stylesheet() -> FileResponse:
        return FileResponse(ASSETS / "console.css", media_type="text/css")

    @app.get("/human/state", dependencies=[Depends(human)])
    @app.get("/agent/state", dependencies=[Depends(agent)])
    async def state() -> State:
        return await browser_session().status()

    @app.post("/human/control", dependencies=[Depends(human)])
    async def control(command: Control) -> State:
        return await browser_session().control(command)

    @app.post("/human/navigate", dependencies=[Depends(human)])
    async def navigate(command: Navigate) -> State:
        return await browser_session().navigate(command)

    @app.post("/human/input", dependencies=[Depends(human)])
    async def browser_input(command: HumanInput) -> State:
        return await browser_session().input(command)

    @app.get("/human/frame", dependencies=[Depends(human)])
    async def frame() -> Response:
        return Response(await browser_session().frame(), media_type="image/jpeg")

    @app.post("/human/close", dependencies=[Depends(human)])
    async def close() -> State:
        return await browser_session().close()

    @app.get("/human/demo", dependencies=[Depends(human)])
    async def demo_access() -> dict[str, str]:
        if demo is None:
            raise HTTPException(404, "demo_disabled")
        return {"url": demo[0] + "/login", "password": demo[1]}

    @app.post("/human/demo-run", dependencies=[Depends(human)])
    async def demo_run() -> View:
        current = browser_session()
        if demo is None or current.page.url != demo[0] + "/workspace":
            raise HTTPException(409, "open_demo_workspace_first")
        view = await current.execute(
            AgentCommand(operation="read", revision=current.revision)
        )
        actions: tuple[tuple[Literal["fill", "select", "click"], str, str], ...] = (
            ("fill", "Project", "Browser feasibility"),
            ("select", "Category", "research"),
            ("click", "Save project", ""),
        )
        for operation, label, value in actions:
            element = next(
                (item for item in view.elements if item.label == label), None
            )
            if element is None:
                raise HTTPException(409, "demo_form_not_available")
            view = await current.execute(
                AgentCommand(
                    operation=operation,
                    revision=view.revision,
                    element_id=element.id,
                    value=value,
                )
            )
        return view

    @app.post("/agent", dependencies=[Depends(agent)])
    async def execute(command: AgentCommand) -> View:
        return await browser_session().execute(command)

    return app


async def check_browser_installation() -> None:
    async with async_playwright() as playwright:
        chromium_executable(playwright)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--mode", choices=MODES, default="headful_cdp")
    parser.add_argument("--no-demo", action="store_true")
    parser.add_argument(
        "--access-file",
        type=Path,
        default=Path("validation/runs/browser-handoff/console-access.json"),
    )
    args = parser.parse_args()
    try:
        asyncio.run(check_browser_installation())
    except BrowserInstallError as exc:
        parser.exit(1, f"ERROR: {exc}\n")
    options = Options(
        secrets.token_urlsafe(32),
        secrets.token_urlsafe(32),
        mode=args.mode,
        demo=not args.no_demo,
    )
    access = {
        "url": f"http://127.0.0.1:{args.port}/",
        "human_token": options.human_token,
        "agent_token": options.agent_token,
    }
    args.access_file.parent.mkdir(parents=True, exist_ok=True)
    with os.fdopen(
        os.open(args.access_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w"
    ) as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(access, handle)
    print(f"Console: {access['url']} (access codes: {args.access_file})", flush=True)
    uvicorn.run(
        create_app(options),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
