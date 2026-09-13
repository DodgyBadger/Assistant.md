"""Browser-facing installation-owner authentication surfaces."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.requests import ClientDisconnect

from core.authentication import (
    OWNER_CSRF_COOKIE,
    OWNER_SESSION_COOKIE,
    AuthenticationFailureLimiter,
    AuthenticationMode,
    AuthenticationPolicy,
    OwnerSessionCodec,
)

router = APIRouter(prefix="/auth", tags=["Assistant.md authentication"])
_COOKIE_MAX_AGE_SECONDS = 12 * 60 * 60
_MAXIMUM_SESSION_EXCHANGE_BYTES = 8192
_COOKIE_EXPIRY_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class _DuplicateJSONKey(ValueError):
    """Raised when a credential request contains ambiguous duplicate keys."""


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def owner_login(request: Request) -> Response:
    """Render the dependency-free owner-token sign-in page."""
    policy = _policy(request)
    if policy.mode is not AuthenticationMode.OWNER_TOKEN:
        return RedirectResponse(url="/", status_code=303)
    return HTMLResponse(_login_page())


@router.post("/session", include_in_schema=False)
async def create_owner_session(
    request: Request,
) -> JSONResponse:
    """Exchange the owner token for a signed HttpOnly browser session."""
    policy = _policy(request)
    if policy.mode is not AuthenticationMode.OWNER_TOKEN:
        raise HTTPException(status_code=404, detail="Not found.")
    limiter = _failure_limiter(request)
    peer_key = _peer_key(request)
    if limiter.is_limited(peer_key):
        raise HTTPException(
            status_code=429,
            detail="Too many authentication failures.",
            headers={"Retry-After": "60"},
        )
    supplied_token = await _read_owner_token(request)
    if policy.authenticate_owner_bearer(supplied_token) is None:
        limiter.record_failure(peer_key)
        raise HTTPException(status_code=401, detail="Authentication failed.")
    limiter.record_success(peer_key)
    issued = OwnerSessionCodec(policy).issue()
    response = JSONResponse({"authenticated": True})
    secure = _secure_cookie(request)
    response.set_cookie(
        OWNER_SESSION_COOKIE,
        issued.cookie_value,
        max_age=_COOKIE_MAX_AGE_SECONDS,
        expires=issued.expires_at,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        OWNER_CSRF_COOKIE,
        issued.csrf_token,
        max_age=_COOKIE_MAX_AGE_SECONDS,
        expires=issued.expires_at,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/logout", include_in_schema=False)
async def delete_owner_session(request: Request) -> JSONResponse:
    """Clear both browser session cookies after middleware CSRF verification."""
    response = JSONResponse({"authenticated": False})
    secure = _secure_cookie(request)
    response.set_cookie(
        OWNER_SESSION_COOKIE,
        "",
        max_age=0,
        expires=_COOKIE_EXPIRY_EPOCH,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        OWNER_CSRF_COOKIE,
        "",
        max_age=0,
        expires=_COOKIE_EXPIRY_EPOCH,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _policy(request: Request) -> AuthenticationPolicy:
    policy = getattr(request.app.state, "authentication_policy", None)
    if not isinstance(policy, AuthenticationPolicy):
        raise RuntimeError("Authentication policy is unavailable.")
    return policy


def _secure_cookie(request: Request) -> bool:
    return request.url.scheme == "https"


async def _read_owner_token(request: Request) -> str:
    """Read one bounded JSON credential without reflecting invalid input."""
    content_type = (request.headers.get("content-type") or "").split(";", 1)[0]
    if content_type.strip().lower() != "application/json":
        raise HTTPException(
            status_code=400, detail="Authentication request is invalid."
        )
    body = bytearray()
    try:
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > _MAXIMUM_SESSION_EXCHANGE_BYTES:
                raise HTTPException(
                    status_code=400,
                    detail="Authentication request is invalid.",
                )
    except ClientDisconnect as exc:
        raise HTTPException(
            status_code=400,
            detail="Authentication request is invalid.",
        ) from exc
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONKey) as exc:
        raise HTTPException(
            status_code=400,
            detail="Authentication request is invalid.",
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"owner_token"}:
        raise HTTPException(
            status_code=400, detail="Authentication request is invalid."
        )
    owner_token = payload["owner_token"]
    if not isinstance(owner_token, str) or not (1 <= len(owner_token) <= 4096):
        raise HTTPException(
            status_code=400, detail="Authentication request is invalid."
        )
    return owner_token


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


def _failure_limiter(request: Request) -> AuthenticationFailureLimiter:
    limiter = getattr(request.app.state, "authentication_failure_limiter", None)
    if not isinstance(limiter, AuthenticationFailureLimiter):
        raise RuntimeError("Authentication failure limiter is unavailable.")
    return limiter


def _peer_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _login_page() -> str:
    title = escape("Assistant.md owner sign-in")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; min-height: 100vh;
      display: grid; place-items: center; background: #111827; color: #f9fafb; }}
    main {{ width: min(28rem, calc(100% - 2rem)); }}
    form {{ display: grid; gap: 1rem; padding: 1.5rem; border: 1px solid #374151;
      border-radius: .75rem; background: #1f2937; }}
    input, button {{ font: inherit; padding: .75rem; border-radius: .5rem; }}
    input {{ border: 1px solid #4b5563; background: #111827; color: inherit; }}
    button {{ border: 0; background: #2563eb; color: white; cursor: pointer; }}
    #error {{ min-height: 1.25rem; color: #fca5a5; }}
  </style>
</head>
<body><main><form id="login-form">
  <h1>{title}</h1>
  <label for="owner-token">Owner token</label>
  <input id="owner-token" type="password" autocomplete="current-password" required>
  <button type="submit">Sign in</button><div id="error" role="alert"></div>
</form></main>
<script>
const form = document.getElementById('login-form');
const error = document.getElementById('error');
form.addEventListener('submit', async (event) => {{
  event.preventDefault(); error.textContent = '';
  const token = document.getElementById('owner-token').value;
  try {{
    const response = await fetch('/auth/session', {{method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{owner_token: token}})}});
    if (!response.ok) throw new Error();
    window.location.replace('/');
  }} catch (_) {{ error.textContent = 'Authentication failed.'; }}
}});
</script></body></html>"""
