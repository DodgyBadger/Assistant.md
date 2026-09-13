"""Default-deny ASGI ingress-authentication middleware."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie

from starlette.types import ASGIApp, Receive, Scope, Send

from .models import AuthenticatedIdentity, AuthenticationMechanism, AuthenticationMode
from .policy import AuthenticationPolicy
from .rate_limit import AuthenticationFailureLimiter
from .session import OwnerSessionCodec, SessionVerificationError, VerifiedOwnerSession

OWNER_SESSION_COOKIE = "assistantmd_owner_session"
OWNER_CSRF_COOKIE = "assistantmd_csrf"
CSRF_HEADER = "x-assistantmd-csrf"
MAXIMUM_REQUEST_HEADER_BYTES = 64 * 1024
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class PublicRoute:
    """One deliberately unauthenticated method/path pair."""

    method: str
    path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", self.method.upper())
        if not self.path.startswith("/"):
            raise ValueError("Public route paths must be absolute.")


DEFAULT_PUBLIC_ROUTES = frozenset(
    {
        PublicRoute("GET", "/api/health"),
        PublicRoute("HEAD", "/api/health"),
        PublicRoute("GET", "/auth/login"),
        PublicRoute("POST", "/auth/session"),
    }
)


class AuthenticationMiddleware:
    """Authenticate every HTTP/WebSocket scope before route resolution."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        policy: AuthenticationPolicy,
        failure_limiter: AuthenticationFailureLimiter | None = None,
        public_routes: Iterable[PublicRoute] = DEFAULT_PUBLIC_ROUTES,
    ) -> None:
        self._app = app
        self._policy = policy
        self._failure_limiter = failure_limiter or AuthenticationFailureLimiter()
        self._public_routes = frozenset(public_routes)
        self._session_codec = (
            OwnerSessionCodec(policy)
            if policy.mode is AuthenticationMode.OWNER_TOKEN
            else None
        )

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        scope_type = scope.get("type")
        if scope_type not in {"http", "websocket"}:
            await self._app(scope, receive, send)
            return
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "")
        if not _headers_within_limit(scope):
            await _reject(
                scope_type,
                send,
                status_code=431,
                detail="Request headers are too large.",
            )
            return
        if scope_type == "http" and PublicRoute(method, path) in self._public_routes:
            await self._app(scope, receive, send)
            return

        peer_key = _peer_host(scope) or "unknown"
        if self._failure_limiter.is_limited(peer_key):
            await _reject(
                scope_type,
                send,
                status_code=429,
                detail="Too many authentication failures.",
            )
            return
        admission = self._authenticate(scope)
        if admission is None:
            self._failure_limiter.record_failure(peer_key)
            if (
                scope_type == "http"
                and method == "GET"
                and path == "/"
                and self._policy.mode is AuthenticationMode.OWNER_TOKEN
            ):
                await _redirect_to_login(send)
                return
            await _reject(
                scope_type, send, status_code=401, detail="Authentication required."
            )
            return
        identity, owner_session = admission
        self._failure_limiter.record_success(peer_key)
        if (
            scope_type == "http"
            and method not in _SAFE_METHODS
            and identity.mechanism is AuthenticationMechanism.OWNER_SESSION
        ):
            if owner_session is None or not self._verify_csrf(scope, owner_session):
                await _reject(
                    scope_type,
                    send,
                    status_code=403,
                    detail="CSRF verification failed.",
                )
                return

        state = scope.setdefault("state", {})
        state["authenticated_identity"] = identity
        await self._app(scope, receive, send)

    def _authenticate(
        self, scope: Scope
    ) -> tuple[AuthenticatedIdentity, VerifiedOwnerSession | None] | None:
        mode = self._policy.mode
        if mode is AuthenticationMode.DISABLED:
            identity = self._policy.authenticate_disabled()
            return (identity, None) if identity is not None else None
        peer_host = _peer_host(scope)
        if mode is AuthenticationMode.LOOPBACK:
            identity = self._policy.authenticate_loopback(peer_host)
            return (identity, None) if identity is not None else None
        if mode is AuthenticationMode.TRUSTED_PROXY:
            assertion = _single_header(
                scope,
                self._policy.proxy_assertion_header.lower().encode("ascii"),
            )
            identity = self._policy.authenticate_proxy(
                assertion=assertion,
                peer_host=peer_host,
            )
            return (identity, None) if identity is not None else None

        authorization_headers = _header_values(scope, b"authorization")
        cookie_header = _single_header(scope, b"cookie")
        has_session_cookie = _cookie_name_count(cookie_header, OWNER_SESSION_COOKIE) > 0
        if authorization_headers:
            if len(authorization_headers) != 1 or has_session_cookie:
                return None
            bearer = _bearer_token(scope)
            identity = self._policy.authenticate_owner_bearer(bearer)
            return (identity, None) if identity is not None else None
        session = self._owner_session(scope)
        if session is None:
            return None
        return session.identity, session

    def _owner_session(self, scope: Scope) -> VerifiedOwnerSession | None:
        if self._session_codec is None:
            return None
        cookie_header = _single_header(scope, b"cookie")
        if (
            cookie_header is None
            or _cookie_name_count(cookie_header, OWNER_SESSION_COOKIE) != 1
        ):
            return None
        try:
            cookies = SimpleCookie()
            cookies.load(cookie_header)
        except CookieError:
            return None
        morsel = cookies.get(OWNER_SESSION_COOKIE)
        if morsel is None:
            return None
        try:
            return self._session_codec.verify(morsel.value)
        except SessionVerificationError:
            return None

    def _verify_csrf(
        self,
        scope: Scope,
        session: VerifiedOwnerSession,
    ) -> bool:
        if self._session_codec is None:
            return False
        csrf_token = _single_header(scope, CSRF_HEADER.encode("ascii"))
        try:
            self._session_codec.verify_csrf(session, csrf_token)
        except SessionVerificationError:
            return False
        return True


def get_authenticated_identity(scope: Scope) -> AuthenticatedIdentity | None:
    """Return the identity installed by ingress middleware, if present."""
    state = scope.get("state")
    if not isinstance(state, dict):
        return None
    identity = state.get("authenticated_identity")
    return identity if isinstance(identity, AuthenticatedIdentity) else None


def _single_header(scope: Scope, lower_name: bytes) -> str | None:
    matches = _header_values(scope, lower_name)
    if matches is None or len(matches) != 1:
        return None
    try:
        return matches[0].decode("latin-1")
    except UnicodeDecodeError:
        return None


def _header_values(scope: Scope, lower_name: bytes) -> list[bytes] | None:
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, list):
        return None
    matches: list[bytes] = []
    for raw_header in raw_headers:
        if not isinstance(raw_header, tuple) or len(raw_header) != 2:
            return None
        name, value = raw_header
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            return None
        if name.lower() == lower_name:
            matches.append(value)
    return matches


def _cookie_name_count(cookie_header: str | None, cookie_name: str) -> int:
    if cookie_header is None:
        return 0
    count = 0
    for raw_pair in cookie_header.split(";"):
        name, separator, _ = raw_pair.strip().partition("=")
        if separator and name == cookie_name:
            count += 1
    return count


def _bearer_token(scope: Scope) -> str | None:
    authorization = _single_header(scope, b"authorization")
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token or " " in token:
        return None
    return token


def _peer_host(scope: Scope) -> str | None:
    client = scope.get("client")
    if not isinstance(client, tuple | list) or not client:
        return None
    host = client[0]
    return host if isinstance(host, str) else None


async def _reject(
    scope_type: str,
    send: Send,
    *,
    status_code: int,
    detail: str,
) -> None:
    if scope_type == "websocket":
        await send({"type": "websocket.close", "code": 4401, "reason": detail})
        return
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    if status_code == 429:
        headers.append((b"retry-after", b"60"))
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _redirect_to_login(send: Send) -> None:
    body = b""
    await send(
        {
            "type": "http.response.start",
            "status": 303,
            "headers": [
                (b"location", b"/auth/login"),
                (b"content-length", b"0"),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _headers_within_limit(scope: Scope) -> bool:
    raw_headers = scope.get("headers")
    if not isinstance(raw_headers, list):
        return False
    total = 0
    for raw_header in raw_headers:
        if not isinstance(raw_header, tuple) or len(raw_header) != 2:
            return False
        name, value = raw_header
        if not isinstance(name, bytes) or not isinstance(value, bytes):
            return False
        total += len(name) + len(value)
        if total > MAXIMUM_REQUEST_HEADER_BYTES:
            return False
    return True
