"""Real ingress owner, CSRF, bearer, session, and peer authentication boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from core.authentication import (
    CSRF_HEADER,
    MAXIMUM_REQUEST_HEADER_BYTES,
    OWNER_CSRF_COOKIE,
    OWNER_SESSION_COOKIE,
    OwnerSessionCodec,
    SessionVerificationError,
)
from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.authentication import (
    OWNER_SECRET,
    authentication_application,
    authentication_client,
    owner_policy,
)

pytestmark = pytest.mark.scenario


async def test_health_before_runtime_start(app: Application) -> None:
    app.vault("IngressAuthenticationVault")
    application = authentication_application(
        app, "owner_token", attach_runtime=False, ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
    )
    async with authentication_client(app, application) as client:
        starting = await client.get("/api/health")
        assert starting.status_code == 503
        assert starting.json() == {"status": "starting", "scheduler_running": False}
    await app.start()


async def test_owner_session(app: Application) -> None:
    app.vault("IngressAuthenticationVault")
    await app.start()
    ingress = authentication_application(
        app, "owner_token", ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
    )
    async with (
        authentication_client(app, ingress) as browser,
        authentication_client(app, ingress) as bearer,
    ):
        health = await browser.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {"status": "healthy", "scheduler_running": True}
        root = await browser.get("/")
        assert root.status_code == 303 and root.headers["location"] == "/auth/login"
        for path in ("/api/status", "/docs", "/static/js/authentication.js"):
            assert (await browser.get(path)).status_code == 401, path
        login_page = await browser.get("/auth/login")
        assert login_page.status_code == 200 and OWNER_SECRET not in login_page.text
        assert (
            await browser.post("/auth/session", json={"owner_token": "wrong"})
        ).status_code == 401
        login = await browser.post("/auth/session", json={"owner_token": OWNER_SECRET})
        assert login.status_code == 200 and login.json() == {"authenticated": True}
        assert login.headers["cache-control"].startswith("no-store")
        cookies = login.headers.get_list("set-cookie")
        assert len(cookies) == 2
        assert any(
            OWNER_SESSION_COOKIE in item and "HttpOnly" in item and ("Secure" in item)
            for item in cookies
        )
        assert any(
            OWNER_CSRF_COOKIE in item and "HttpOnly" not in item and ("Secure" in item)
            for item in cookies
        )
        assert all(OWNER_SECRET not in item for item in cookies)
        csrf = browser.cookies.get(OWNER_CSRF_COOKIE)
        assert csrf
        assert (await browser.get("/")).status_code == 200
        assert (await browser.get("/api/status")).status_code == 200
        async with authentication_client(app, ingress, "192.0.2.2") as other_browser:
            assert (
                await other_browser.post(
                    "/auth/session", json={"owner_token": OWNER_SECRET}
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
        rescan = await browser.post("/api/vaults/rescan", headers={CSRF_HEADER: csrf})
        assert rescan.status_code == 200 and rescan.json()["success"] is True
        headers = {"Authorization": f"Bearer {OWNER_SECRET}"}
        assert (await bearer.get("/api/status", headers=headers)).status_code == 200
        activity = await bearer.get("/api/system/activity-log", headers=headers)
        assert activity.status_code == 200
        auth_events = [
            entry
            for entry in activity.json()["entries"]
            if entry.get("tag") == "authentication"
        ]
        assert any(
            entry.get("data", {}).get("event") == "owner_session_exchange_failed"
            and entry["data"].get("status") == "failed"
            and (entry["data"].get("reason") == "invalid_credential")
            for entry in auth_events
        )
        assert any(
            entry.get("data", {}).get("event") == "owner_session_exchange_completed"
            and entry["data"].get("status") == "completed"
            and (entry["data"].get("secure_cookie") is True)
            for entry in auth_events
        )
        assert OWNER_SECRET not in json.dumps(auth_events)
        bearer_rescan = await bearer.post("/api/vaults/rescan", headers=headers)
        assert (
            bearer_rescan.status_code == 200 and bearer_rescan.json()["success"] is True
        )
        assert (
            await bearer.get("/api/status", headers={"Authorization": "Bearer wrong"})
        ).status_code == 401
        assert (await browser.get("/api/status", headers=headers)).status_code == 401
        session = browser.cookies.get(OWNER_SESSION_COOKIE)
        assert session and OWNER_SECRET not in session
        for invalid_session in ("malformed", session + "tampered"):
            assert (
                await bearer.get(
                    "/api/status",
                    headers={"Cookie": f"{OWNER_SESSION_COOKIE}={invalid_session}"},
                )
            ).status_code == 401
        rotated = authentication_application(
            app,
            "owner_token",
            ASSISTANTMD_AUTH_SECRET="rotated-owner-secret-" + "y" * 32,
        )
        async with authentication_client(app, rotated) as rotated_client:
            assert (
                await rotated_client.get(
                    "/api/status",
                    headers={"Cookie": f"{OWNER_SESSION_COOKIE}={session}"},
                )
            ).status_code == 401
        duplicate = f"{OWNER_SESSION_COOKIE}=invalid; {OWNER_SESSION_COOKIE}={session}"
        assert (
            await bearer.get("/api/status", headers={"Cookie": duplicate})
        ).status_code == 401
        assert (await browser.post("/auth/logout")).status_code == 403
        logout = await browser.post("/auth/logout", headers={CSRF_HEADER: csrf})
        assert logout.status_code == 200 and logout.json() == {"authenticated": False}
        assert (await browser.get("/")).status_code == 303
        assert (await browser.get("/api/status")).status_code == 401


async def test_session_time_boundaries(app: Application) -> None:
    app.vault("IngressAuthenticationVault")
    await app.start()
    policy = owner_policy(app)
    codec = OwnerSessionCodec(policy, lifetime=timedelta(minutes=5))
    now = datetime.now(UTC).replace(microsecond=0)
    current = codec.issue(now=now - timedelta(minutes=1))
    expired = codec.issue(now=now - timedelta(days=1))
    future = codec.issue(now=now + timedelta(days=1))
    assert OWNER_SECRET not in current.cookie_value
    assert OWNER_SECRET not in current.csrf_token
    assert current.csrf_token not in current.cookie_value
    assert len(current.cookie_value.encode()) <= 4096
    # Public codec clock fixes the exclusive expiration boundary deterministically.
    assert codec.verify(
        current.cookie_value, now=current.expires_at - timedelta(seconds=1)
    )
    with pytest.raises(SessionVerificationError):
        codec.verify(current.cookie_value, now=current.expires_at)
    ingress = authentication_application(
        app, "owner_token", ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
    )
    async with authentication_client(app, ingress) as client:
        accepted = await client.get(
            "/api/status",
            headers={"Cookie": f"{OWNER_SESSION_COOKIE}={current.cookie_value}"},
        )
        assert accepted.status_code == 200
        for value in (
            expired.cookie_value,
            future.cookie_value,
            "",
            "one-segment",
            ".",
            "x.y.z",
            "a" * 4097,
        ):
            denied = await client.get(
                "/api/status", headers={"Cookie": f"{OWNER_SESSION_COOKIE}={value}"}
            )
            assert denied.status_code == 401


async def test_invalid_requests_and_rate_limit(app: Application) -> None:
    app.vault("IngressAuthenticationVault")
    await app.start()
    ingress = authentication_application(
        app, "owner_token", ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
    )
    async with authentication_client(app, ingress) as client:
        oversized_header = await client.get(
            "/api/health", headers={"X-Oversized": "x" * MAXIMUM_REQUEST_HEADER_BYTES}
        )
        assert oversized_header.status_code == 431
        unknown = await client.get("/route-that-does-not-exist")
        assert unknown.status_code == 401
        assert unknown.headers["cache-control"].startswith("no-store")
        for authorization in ("Basic abc", f"Bearer {OWNER_SECRET} trailing"):
            assert (
                await client.get(
                    "/api/status", headers={"Authorization": authorization}
                )
            ).status_code == 401
        oversized = "z" * 4097
        response = await client.post("/auth/session", json={"owner_token": oversized})
        assert response.status_code == 400 and oversized not in response.text
        body = f'{{"owner_token":"wrong","owner_token":"{OWNER_SECRET}"}}'
        response = await client.post(
            "/auth/session", content=body, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 400 and OWNER_SECRET not in response.text
    # A fresh application isolates rate state from earlier invalid requests.
    ingress = authentication_application(
        app, "owner_token", ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
    )
    async with authentication_client(app, ingress) as client:
        for _ in range(10):
            assert (
                await client.post("/auth/session", json={"owner_token": "wrong"})
            ).status_code == 401
        limited = await client.post("/auth/session", json={"owner_token": "wrong"})
        assert limited.status_code == 429
        assert 0 < int(limited.headers["retry-after"]) <= 60


async def test_peer_modes(app: Application) -> None:
    app.vault("IngressAuthenticationVault")
    await app.start()
    disabled = authentication_application(app, "disabled")
    async with authentication_client(app, disabled) as client:
        for path in ("/", "/api/status", "/docs", "/static/js/authentication.js"):
            assert (await client.get(path)).status_code == 200
        assert (await client.get("/auth/login")).status_code == 303
        assert (
            await client.post("/auth/session", json={"owner_token": OWNER_SECRET})
        ).status_code == 404
    loopback = authentication_application(app, "loopback")
    async with (
        authentication_client(app, loopback, "127.0.0.1") as local,
        authentication_client(app, loopback) as remote,
    ):
        assert (await local.get("/")).status_code == 200
        assert (await local.get("/api/status")).status_code == 200
        denied = await remote.get(
            "/api/status",
            headers={"Forwarded": "for=127.0.0.1", "X-Forwarded-For": "127.0.0.1"},
        )
        assert denied.status_code == 401
    proxy = authentication_application(
        app,
        "trusted_proxy",
        ASSISTANTMD_AUTH_SECRET=OWNER_SECRET,
        ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS="172.20.0.0/24",
    )
    headers = {"X-AssistantMD-Proxy-Assertion": OWNER_SECRET}
    async with (
        authentication_client(app, proxy, "172.20.0.4") as trusted,
        authentication_client(app, proxy) as untrusted,
    ):
        assert (await trusted.get("/", headers=headers)).status_code == 200
        assert (await trusted.get("/")).status_code == 401
        assert (await trusted.get("/api/status", headers=headers)).status_code == 200
        assert (await trusted.get("/api/status")).status_code == 401
        assert (await untrusted.get("/api/status", headers=headers)).status_code == 401
        duplicate = [("X-AssistantMD-Proxy-Assertion", OWNER_SECRET)] * 2
        assert (await trusted.get("/api/status", headers=duplicate)).status_code == 401
