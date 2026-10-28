"""Real authentication application variants and recorded ASGI transport."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from fastapi import FastAPI

from validation.scenarios.application import Application

if TYPE_CHECKING:
    from core.authentication import AuthenticationPolicy

OWNER_SECRET = "validation-owner-secret-" + "x" * 32


def owner_policy(app: Application) -> AuthenticationPolicy:
    from core.authentication import load_authentication_policy
    from core.settings import AppSettings

    app.evidence.register_secret(OWNER_SECRET)
    return load_authentication_policy(
        AppSettings.model_validate(
            {
                "ASSISTANTMD_AUTH_MODE": "owner_token",
                "ASSISTANTMD_AUTH_SECRET": OWNER_SECRET,
            }
        )
    )


def authentication_application(
    app: Application, mode: str, *, attach_runtime: bool = True, **settings: str
) -> FastAPI:
    from api.application import create_application
    from core.authentication import load_authentication_policy
    from core.runtime.state import get_runtime_context
    from core.settings import AppSettings

    app.evidence.register_secret(OWNER_SECRET)
    if secret := settings.get("ASSISTANTMD_AUTH_SECRET"):
        app.evidence.register_secret(secret)
    policy = load_authentication_policy(
        AppSettings.model_validate({"ASSISTANTMD_AUTH_MODE": mode, **settings})
    )
    application = create_application(authentication_policy=policy)
    if attach_runtime:
        application.state.runtime = get_runtime_context()
    return application


def authentication_client(
    app: Application, application: FastAPI, peer: str = "192.0.2.1"
) -> httpx.AsyncClient:
    async def record_response(response: httpx.Response) -> None:
        await response.aread()
        for cookie in response.cookies.jar:
            if cookie.value:
                app.evidence.register_secret(cookie.value)
        try:
            body = response.json()
        except ValueError:
            body = response.text
        app.evidence.append(
            "ingress-responses.jsonl",
            {
                "peer": peer,
                "method": response.request.method,
                "path": response.request.url.path,
                "status": response.status_code,
                "headers": list(response.headers.multi_items()),
                "response": body,
            },
        )

    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application, client=(peer, 50000)),
        base_url="https://assistant.example.test",
        follow_redirects=False,
        event_hooks={"response": [record_response]},
    )
