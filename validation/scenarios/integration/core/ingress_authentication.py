"""Exercise real ingress authentication against a running AssistantMD runtime."""

from __future__ import annotations

import httpx
from fastapi import FastAPI

from api.application import create_application
from core.authentication import (
    CSRF_HEADER,
    MAXIMUM_REQUEST_HEADER_BYTES,
    OWNER_CSRF_COOKIE,
    OWNER_SESSION_COOKIE,
    load_authentication_policy,
)
from core.runtime.state import get_runtime_context
from core.settings import AppSettings
from validation.core.base_scenario import BaseScenario

_SECRET = "validation-owner-secret-" + "x" * 32


def _application(mode: str, **settings: str) -> FastAPI:
    policy = load_authentication_policy(
        AppSettings(ASSISTANTMD_AUTH_MODE=mode, **settings)
    )
    app = create_application(authentication_policy=policy)
    app.state.runtime = get_runtime_context()
    return app


def _client(app: FastAPI, peer: str = "192.0.2.1") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(peer, 50000)),
        base_url="https://assistant.example.test",
        follow_redirects=False,
    )


class IngressAuthenticationScenario(BaseScenario):
    """Protect UI and runtime operations across owner, proxy and loopback modes."""

    async def test_scenario(self) -> None:
        self.create_vault("IngressAuthenticationVault")
        try:
            await self.start_system()
            await self._owner_session()
            await self._invalid_requests_and_rate_limit()
            await self._peer_modes()
        finally:
            await self.stop_system()
            self.teardown_scenario()

    async def _owner_session(self) -> None:
        app = _application("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
        async with _client(app) as browser, _client(app) as bearer:
            health = await browser.get("/api/health")
            assert health.status_code == 200
            assert health.json() == {"status": "healthy", "scheduler_running": True}

            root = await browser.get("/")
            assert root.status_code == 303 and root.headers["location"] == "/auth/login"
            for path in ("/api/status", "/docs", "/static/js/authentication.js"):
                assert (await browser.get(path)).status_code == 401, path
            assert (await browser.get("/auth/login")).status_code == 200
            assert (
                await browser.post("/auth/session", json={"owner_token": "wrong"})
            ).status_code == 401

            login = await browser.post("/auth/session", json={"owner_token": _SECRET})
            assert login.status_code == 200 and login.json() == {"authenticated": True}
            cookies = login.headers.get_list("set-cookie")
            assert len(cookies) == 2
            assert any(
                OWNER_SESSION_COOKIE in item and "HttpOnly" in item and "Secure" in item
                for item in cookies
            )
            assert any(
                OWNER_CSRF_COOKIE in item
                and "HttpOnly" not in item
                and "Secure" in item
                for item in cookies
            )
            assert all(_SECRET not in item for item in cookies)
            csrf = browser.cookies.get(OWNER_CSRF_COOKIE)
            assert csrf
            assert (await browser.get("/")).status_code == 200
            assert (await browser.get("/api/status")).status_code == 200
            async with _client(app, "192.0.2.2") as other_browser:
                assert (
                    await other_browser.post(
                        "/auth/session", json={"owner_token": _SECRET}
                    )
                ).status_code == 200
                other_csrf = other_browser.cookies.get(OWNER_CSRF_COOKIE)
                assert other_csrf and other_csrf != csrf
                assert (
                    await browser.post(
                        "/api/vaults/rescan", headers={CSRF_HEADER: other_csrf}
                    )
                ).status_code == 403
            assert (await browser.post("/api/vaults/rescan")).status_code == 403
            assert (
                await browser.post("/api/vaults/rescan", headers={CSRF_HEADER: "wrong"})
            ).status_code == 403
            rescan = await browser.post(
                "/api/vaults/rescan", headers={CSRF_HEADER: csrf}
            )
            assert rescan.status_code == 200 and rescan.json()["success"] is True

            headers = {"Authorization": f"Bearer {_SECRET}"}
            assert (await bearer.get("/api/status", headers=headers)).status_code == 200
            bearer_rescan = await bearer.post("/api/vaults/rescan", headers=headers)
            assert (
                bearer_rescan.status_code == 200
                and bearer_rescan.json()["success"] is True
            )
            assert (
                await bearer.get(
                    "/api/status", headers={"Authorization": "Bearer wrong"}
                )
            ).status_code == 401
            assert (
                await browser.get("/api/status", headers=headers)
            ).status_code == 401
            session = browser.cookies.get(OWNER_SESSION_COOKIE)
            assert session and _SECRET not in session
            for invalid_session in ("malformed", session + "tampered"):
                assert (
                    await bearer.get(
                        "/api/status",
                        headers={"Cookie": f"{OWNER_SESSION_COOKIE}={invalid_session}"},
                    )
                ).status_code == 401
            rotated = _application(
                "owner_token",
                ASSISTANTMD_AUTH_SECRET="rotated-owner-secret-" + "y" * 32,
            )
            async with _client(rotated) as rotated_client:
                assert (
                    await rotated_client.get(
                        "/api/status",
                        headers={"Cookie": f"{OWNER_SESSION_COOKIE}={session}"},
                    )
                ).status_code == 401
            duplicate = (
                f"{OWNER_SESSION_COOKIE}=invalid; {OWNER_SESSION_COOKIE}={session}"
            )
            assert (
                await bearer.get("/api/status", headers={"Cookie": duplicate})
            ).status_code == 401

            assert (await browser.post("/auth/logout")).status_code == 403
            logout = await browser.post("/auth/logout", headers={CSRF_HEADER: csrf})
            assert logout.status_code == 200 and logout.json() == {
                "authenticated": False
            }
            assert (await browser.get("/")).status_code == 303
            assert (await browser.get("/api/status")).status_code == 401

    async def _invalid_requests_and_rate_limit(self) -> None:
        app = _application("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
        async with _client(app) as client:
            oversized_header = await client.get(
                "/api/health",
                headers={"X-Oversized": "x" * MAXIMUM_REQUEST_HEADER_BYTES},
            )
            assert oversized_header.status_code == 431
            unknown = await client.get("/route-that-does-not-exist")
            assert unknown.status_code == 401
            assert unknown.headers["cache-control"].startswith("no-store")
            for authorization in ("Basic abc", f"Bearer {_SECRET} trailing"):
                assert (
                    await client.get(
                        "/api/status", headers={"Authorization": authorization}
                    )
                ).status_code == 401
            oversized = "z" * 4097
            response = await client.post(
                "/auth/session", json={"owner_token": oversized}
            )
            assert response.status_code == 400 and oversized not in response.text
            body = f'{{"owner_token":"wrong","owner_token":"{_SECRET}"}}'
            response = await client.post(
                "/auth/session",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 400 and _SECRET not in response.text

        # Use a separate peer and application so previous failures cannot affect the limit.
        app = _application("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
        async with _client(app) as client:
            for _ in range(10):
                assert (
                    await client.post("/auth/session", json={"owner_token": "wrong"})
                ).status_code == 401
            limited = await client.post("/auth/session", json={"owner_token": "wrong"})
            assert limited.status_code == 429
            assert 0 < int(limited.headers["retry-after"]) <= 60

    async def _peer_modes(self) -> None:
        disabled = _application("disabled")
        async with _client(disabled) as client:
            for path in ("/", "/api/status", "/docs", "/static/js/authentication.js"):
                assert (await client.get(path)).status_code == 200
            assert (await client.get("/auth/login")).status_code == 303
            assert (
                await client.post("/auth/session", json={"owner_token": _SECRET})
            ).status_code == 404
        loopback = _application("loopback")
        async with _client(loopback, "127.0.0.1") as local, _client(loopback) as remote:
            assert (await local.get("/api/status")).status_code == 200
            denied = await remote.get(
                "/api/status",
                headers={"Forwarded": "for=127.0.0.1", "X-Forwarded-For": "127.0.0.1"},
            )
            assert denied.status_code == 401

        proxy = _application(
            "trusted_proxy",
            ASSISTANTMD_AUTH_SECRET=_SECRET,
            ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS="172.20.0.0/24",
        )
        headers = {"X-AssistantMD-Proxy-Assertion": _SECRET}
        async with _client(proxy, "172.20.0.4") as trusted, _client(proxy) as untrusted:
            assert (
                await trusted.get("/api/status", headers=headers)
            ).status_code == 200
            assert (await trusted.get("/api/status")).status_code == 401
            assert (
                await untrusted.get("/api/status", headers=headers)
            ).status_code == 401
            duplicate = [("X-AssistantMD-Proxy-Assertion", _SECRET)] * 2
            assert (
                await trusted.get("/api/status", headers=duplicate)
            ).status_code == 401
