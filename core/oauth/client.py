"""Async OAuth token endpoint client shared by provider adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx


class OAuthTokenExchangeError(ValueError):
    """Raised when an OAuth token endpoint rejects or malforms a response."""


@dataclass(frozen=True)
class OAuthTokenResponse:
    """Normalized authorization-code or refresh-token response."""

    access_token: str
    token_type: str
    expires_in: int | None
    refresh_token: str | None
    scopes: tuple[str, ...]


OAuthHTTPClientFactory = Callable[[], httpx.AsyncClient]


async def request_oauth_token(
    *,
    token_endpoint: str,
    form: Mapping[str, str],
    http_client_factory: OAuthHTTPClientFactory,
) -> OAuthTokenResponse:
    """Submit a form-encoded token request and validate its safe fields."""
    try:
        async with http_client_factory() as client:
            response = await client.post(
                token_endpoint,
                data=dict(form),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError
        return _parse_token_response(payload)
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise OAuthTokenExchangeError("The OAuth token request was rejected.") from exc


def _parse_token_response(payload: dict[str, Any]) -> OAuthTokenResponse:
    access_token = payload.get("access_token")
    token_type = payload.get("token_type", "Bearer")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("OAuth token response omitted access_token.")
    if not isinstance(token_type, str) or token_type.lower() != "bearer":
        raise ValueError("OAuth token response has unsupported token_type.")
    raw_expires = payload.get("expires_in")
    expires_in = None
    if raw_expires is not None:
        expires_in = int(raw_expires)
        if expires_in <= 0:
            raise ValueError("OAuth token response has invalid expires_in.")
    refresh_token = payload.get("refresh_token")
    if refresh_token is not None and (
        not isinstance(refresh_token, str) or not refresh_token
    ):
        raise ValueError("OAuth token response has invalid refresh_token.")
    raw_scope = payload.get("scope", "")
    if not isinstance(raw_scope, str):
        raise ValueError("OAuth token response has invalid scope.")
    scopes = tuple(dict.fromkeys(scope for scope in raw_scope.split() if scope))
    return OAuthTokenResponse(
        access_token=access_token,
        token_type=token_type,
        expires_in=expires_in,
        refresh_token=refresh_token,
        scopes=scopes,
    )
