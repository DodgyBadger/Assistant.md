"""Production-topology tests for AssistantMD ingress authentication."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from core.authentication import (
    CSRF_HEADER,
    OWNER_CSRF_COOKIE,
    OWNER_SESSION_COOKIE,
    load_authentication_policy,
)
from core.runtime.paths import set_bootstrap_roots
from core.settings import AppSettings

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="assistantmd-auth-application-")
_TEST_ROOT_PATH = Path(_TEST_ROOT.name)
set_bootstrap_roots(_TEST_ROOT_PATH / "data", _TEST_ROOT_PATH / "system")

_SECRET = "a" * 32


def _client(
    mode: str,
    *,
    client_host: str = "testclient",
    base_url: str = "http://testserver",
    **values: str,
) -> TestClient:
    from api.application import create_application

    policy = load_authentication_policy(
        AppSettings(ASSISTANTMD_AUTH_MODE=mode, **values)
    )
    app = create_application(authentication_policy=policy)
    app.state.runtime = None
    return TestClient(app, base_url=base_url, client=(client_host, 50000))


def test_disabled_mode_preserves_open_ui_and_api() -> None:
    client = _client("disabled")

    assert client.get("/", follow_redirects=False).status_code == 200
    assert client.get("/static/js/authentication.js").status_code == 200
    assert client.get("/api/status").status_code != 401
    assert client.get("/docs").status_code == 200


def test_public_health_does_not_require_principal_authority() -> None:
    client = _client("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "starting", "scheduler_running": False}


def test_owner_mode_redirects_root_but_denies_api_and_docs() -> None:
    client = _client("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)

    root = client.get("/", follow_redirects=False)

    assert root.status_code == 303
    assert root.headers["location"] == "/auth/login"
    assert client.get("/api/status").status_code == 401
    assert client.get("/docs").status_code == 401
    assert client.get("/static/js/authentication.js").status_code == 401


def test_owner_login_exchange_sets_secure_bounded_cookies() -> None:
    client = _client(
        "owner_token",
        base_url="https://assistant.example.test",
        ASSISTANTMD_AUTH_SECRET=_SECRET,
    )

    login = client.get("/auth/login")
    failed = client.post("/auth/session", json={"owner_token": "b" * 32})
    response = client.post("/auth/session", json={"owner_token": _SECRET})

    assert login.status_code == 200
    assert _SECRET not in login.text
    assert failed.status_code == 401
    assert response.status_code == 200
    assert response.json() == {"authenticated": True}
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert len(set_cookie_headers) == 2
    assert any(
        OWNER_SESSION_COOKIE in header and "HttpOnly" in header and "Secure" in header
        for header in set_cookie_headers
    )
    assert any(
        OWNER_CSRF_COOKIE in header and "HttpOnly" not in header and "Secure" in header
        for header in set_cookie_headers
    )
    assert all(_SECRET not in header for header in set_cookie_headers)
    assert response.headers["cache-control"].startswith("no-store")
    assert client.get("/").status_code == 200


def test_owner_logout_requires_csrf_and_clears_session() -> None:
    client = _client(
        "owner_token",
        base_url="https://assistant.example.test",
        ASSISTANTMD_AUTH_SECRET=_SECRET,
    )
    assert (
        client.post("/auth/session", json={"owner_token": _SECRET}).status_code == 200
    )
    csrf_token = client.cookies.get(OWNER_CSRF_COOKIE)
    assert csrf_token

    assert client.post("/auth/logout").status_code == 403
    response = client.post("/auth/logout", headers={CSRF_HEADER: csrf_token})

    assert response.status_code == 200
    assert response.json() == {"authenticated": False}
    assert client.get("/", follow_redirects=False).status_code == 303


def test_oversized_owner_exchange_is_rejected_without_echo() -> None:
    client = _client("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    oversized = "z" * 4097

    response = client.post("/auth/session", json={"owner_token": oversized})

    assert response.status_code == 400
    assert oversized not in response.text


def test_duplicate_owner_token_keys_are_rejected() -> None:
    client = _client("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    body = f'{{"owner_token":"wrong","owner_token":"{_SECRET}"}}'

    response = client.post(
        "/auth/session",
        content=body,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert _SECRET not in response.text


def test_failed_owner_logins_are_rate_limited() -> None:
    client = _client("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)

    failures = [
        client.post("/auth/session", json={"owner_token": "b" * 32}) for _ in range(10)
    ]
    limited = client.post("/auth/session", json={"owner_token": "b" * 32})

    assert all(response.status_code == 401 for response in failures)
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Too many authentication failures."}
    assert limited.headers["retry-after"] == "60"


def test_loopback_and_trusted_proxy_reach_ui_without_second_login() -> None:
    loopback = _client("loopback", client_host="127.0.0.1")
    proxy = _client(
        "trusted_proxy",
        client_host="172.20.0.4",
        ASSISTANTMD_AUTH_SECRET=_SECRET,
        ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS="172.20.0.0/24",
    )

    assert loopback.get("/").status_code == 200
    assert (
        proxy.get("/", headers={"X-AssistantMD-Proxy-Assertion": _SECRET}).status_code
        == 200
    )
    assert proxy.get("/").status_code == 401


def test_login_surfaces_are_disabled_outside_owner_mode() -> None:
    client = _client("disabled")

    assert client.get("/auth/login", follow_redirects=False).status_code == 303
    assert (
        client.post("/auth/session", json={"owner_token": _SECRET}).status_code == 404
    )
