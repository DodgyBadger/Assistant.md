"""Shared FastAPI application composition root."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Lifespan

from core.advanced_shell import AdvancedShellConfig
from core.advanced_shell.preflight import AdvancedShellPreflightService
from core.authentication import (
    AuthenticationFailureLimiter,
    AuthenticationMiddleware,
    AuthenticationPolicy,
)

from .authentication import router as authentication_router
from .endpoints import public_router, register_exception_handlers
from .endpoints import router as api_router


def create_application(
    *,
    authentication_policy: AuthenticationPolicy,
    advanced_shell_config: AdvancedShellConfig | None = None,
    advanced_shell_preflight: AdvancedShellPreflightService | None = None,
    lifespan: Lifespan[FastAPI] | None = None,
    include_ui: bool = True,
) -> FastAPI:
    """Build the production-equivalent route and middleware topology."""
    app = FastAPI(lifespan=lifespan)
    app.state.authentication_policy = authentication_policy
    effective_shell_config = (
        advanced_shell_config or AdvancedShellConfig.restricted_default()
    )
    app.state.advanced_shell_config = effective_shell_config
    app.state.advanced_shell_preflight = (
        advanced_shell_preflight
        or AdvancedShellPreflightService(effective_shell_config, Path("/app/system"))
    )
    failure_limiter = AuthenticationFailureLimiter()
    app.state.authentication_failure_limiter = failure_limiter
    app.include_router(public_router)
    app.include_router(authentication_router)
    app.include_router(api_router)
    register_exception_handlers(app)

    @app.middleware("http")
    async def prevent_runtime_response_caching(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Keep the UI and runtime surfaces out of proxy/browser caches."""
        response = await call_next(request)
        path = request.url.path
        if (
            path == "/"
            or path.startswith("/api/")
            or path.startswith("/auth/")
            or path.startswith("/static/")
        ):
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    if include_ui:
        static_dir = Path(__file__).resolve().parents[1] / "static"
        app.mount(
            "/static",
            StaticFiles(directory=static_dir, html=True),
            name="static",
        )

        @app.get("/")
        async def root() -> FileResponse:
            return FileResponse(static_dir / "index.html")

    app.add_middleware(
        AuthenticationMiddleware,
        policy=authentication_policy,
        failure_limiter=failure_limiter,
    )
    return app
