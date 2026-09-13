"""Isolated ASGI tests for the default-deny authentication boundary."""

from __future__ import annotations

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from core.authentication import (
    CSRF_HEADER,
    MAXIMUM_REQUEST_HEADER_BYTES,
    OWNER_SESSION_COOKIE,
    AuthenticationMechanism,
    AuthenticationMiddleware,
    AuthenticationPolicy,
    OwnerSessionCodec,
    load_authentication_policy,
)
from core.settings import AppSettings

_SECRET = "a" * 32


def _policy(mode: str, **values: str) -> AuthenticationPolicy:
    return load_authentication_policy(AppSettings(ASSISTANTMD_AUTH_MODE=mode, **values))


def _app(mode: str, **values: str) -> tuple[FastAPI, AuthenticationPolicy]:
    policy = _policy(mode, **values)
    app = FastAPI()
    app.add_middleware(AuthenticationMiddleware, policy=policy)

    @app.get("/api/health")
    async def health() -> dict[str, bool]:
        return {"healthy": True}

    @app.api_route("/protected", methods=["GET", "POST"])
    async def protected(request: Request) -> dict[str, str]:
        identity = request.state.authenticated_identity
        return {"mechanism": identity.mechanism.value}

    @app.websocket("/socket")
    async def socket(websocket: WebSocket) -> None:
        await websocket.accept()
        identity = websocket.state.authenticated_identity
        await websocket.send_text(identity.mechanism.value)
        await websocket.close()

    return app, policy


def test_disabled_mode_opens_http_and_websocket() -> None:
    app, _ = _app("disabled")
    client = TestClient(app)

    response = client.post("/protected")

    assert response.status_code == 200
    assert response.json() == {"mechanism": AuthenticationMechanism.DISABLED.value}
    with client.websocket_connect("/socket") as websocket:
        assert websocket.receive_text() == AuthenticationMechanism.DISABLED.value


def test_public_health_bypasses_authentication() -> None:
    app, _ = _app("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    client = TestClient(app)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"healthy": True}


def test_oversized_headers_are_rejected_even_on_public_routes() -> None:
    app, _ = _app("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    client = TestClient(app)

    response = client.get(
        "/api/health",
        headers={"X-Oversized": "x" * MAXIMUM_REQUEST_HEADER_BYTES},
    )

    assert response.status_code == 431
    assert response.json() == {"detail": "Request headers are too large."}


def test_unclassified_routes_are_protected_before_route_resolution() -> None:
    app, _ = _app("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    client = TestClient(app)

    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 401
    assert response.json() == {"detail": "Authentication required."}
    assert response.headers["cache-control"] == "no-store"


def test_owner_bearer_admits_safe_and_mutating_requests_without_csrf() -> None:
    app, _ = _app("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {_SECRET}"}

    get_response = client.get("/protected", headers=headers)
    post_response = client.post("/protected", headers=headers)

    assert get_response.json() == {
        "mechanism": AuthenticationMechanism.OWNER_BEARER.value
    }
    assert post_response.json() == {
        "mechanism": AuthenticationMechanism.OWNER_BEARER.value
    }


def test_wrong_or_malformed_bearer_is_rejected() -> None:
    app, _ = _app("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    client = TestClient(app)

    assert (
        client.get("/protected", headers={"Authorization": "Basic abc"}).status_code
        == 401
    )
    assert (
        client.get(
            "/protected", headers={"Authorization": f"Bearer {_SECRET} trailing"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/protected", headers={"Authorization": "Bearer " + "b" * 32}
        ).status_code
        == 401
    )


def test_owner_session_requires_csrf_only_for_mutations() -> None:
    app, policy = _app("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    codec = OwnerSessionCodec(policy)
    session = codec.issue()
    client = TestClient(app)
    client.cookies.set(OWNER_SESSION_COOKIE, session.cookie_value)

    get_response = client.get("/protected")
    missing_csrf = client.post("/protected")
    valid_csrf = client.post(
        "/protected",
        headers={CSRF_HEADER: session.csrf_token},
    )

    assert get_response.status_code == 200
    assert get_response.json() == {
        "mechanism": AuthenticationMechanism.OWNER_SESSION.value
    }
    assert missing_csrf.status_code == 403
    assert valid_csrf.status_code == 200


def test_owner_mode_rejects_duplicate_or_mixed_credentials() -> None:
    app, policy = _app("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    session = OwnerSessionCodec(policy).issue()
    client = TestClient(app)

    duplicate_cookie = (
        f"{OWNER_SESSION_COOKIE}=wrong; "
        f"{OWNER_SESSION_COOKIE}={session.cookie_value}"
    )
    mixed = client.get(
        "/protected",
        headers={
            "Authorization": f"Bearer {_SECRET}",
            "Cookie": f"{OWNER_SESSION_COOKIE}={session.cookie_value}",
        },
    )

    assert (
        client.get("/protected", headers={"Cookie": duplicate_cookie}).status_code
        == 401
    )
    assert mixed.status_code == 401


def test_proxy_mode_requires_assertion_and_allowed_immediate_peer() -> None:
    app, _ = _app(
        "trusted_proxy",
        ASSISTANTMD_AUTH_SECRET=_SECRET,
        ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS="172.20.0.0/24",
    )
    allowed_client = TestClient(app, client=("172.20.0.8", 50000))
    denied_client = TestClient(app, client=("172.21.0.8", 50000))
    headers = {"X-AssistantMD-Proxy-Assertion": _SECRET}

    response = allowed_client.get("/protected", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"mechanism": AuthenticationMechanism.TRUSTED_PROXY.value}
    assert denied_client.get("/protected", headers=headers).status_code == 401
    assert allowed_client.get("/protected").status_code == 401


def test_duplicate_proxy_assertions_are_rejected() -> None:
    app, _ = _app("trusted_proxy", ASSISTANTMD_AUTH_SECRET=_SECRET)
    client = TestClient(app)

    response = client.get(
        "/protected",
        headers=[
            ("X-AssistantMD-Proxy-Assertion", _SECRET),
            ("X-AssistantMD-Proxy-Assertion", _SECRET),
        ],
    )

    assert response.status_code == 401


def test_loopback_uses_socket_peer_and_ignores_forwarded_headers() -> None:
    app, _ = _app("loopback")
    loopback_client = TestClient(app, client=("127.0.0.1", 50000))
    docker_client = TestClient(app, client=("172.20.0.8", 50000))

    assert loopback_client.get("/protected").status_code == 200
    response = docker_client.get(
        "/protected",
        headers={"Forwarded": "for=127.0.0.1", "X-Forwarded-For": "127.0.0.1"},
    )
    assert response.status_code == 401


def test_unauthenticated_websocket_closes_before_acceptance() -> None:
    app, _ = _app("owner_token", ASSISTANTMD_AUTH_SECRET=_SECRET)
    client = TestClient(app)

    try:
        with client.websocket_connect("/socket"):
            raise AssertionError("Unauthenticated WebSocket unexpectedly connected.")
    except WebSocketDisconnect as exc:
        assert exc.code == 4401
