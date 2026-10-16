"""OpenAI OAuth token and pending-auth state helpers."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from core.llm.openai_auth import (
    OPENAI_OAUTH_PENDING_SECRET,
    OPENAI_OAUTH_TOKEN_SECRET,
)
from core.settings.secrets_store import (
    delete_secret,
    get_secret_value,
    set_secret_value,
)
from core.utils.provider_errors import provider_response_error_details

OPENAI_OAUTH_INTERNAL_SECRETS = frozenset(
    {OPENAI_OAUTH_TOKEN_SECRET, OPENAI_OAUTH_PENDING_SECRET}
)
OPENAI_OAUTH_ISSUER_URL = "https://auth.openai.com"
OPENAI_OAUTH_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_OAUTH_PENDING_TTL_SECONDS = 600
OPENAI_OAUTH_DEVICE_PENDING_TTL_SECONDS = 15 * 60
OPENAI_OAUTH_REFRESH_WINDOW_SECONDS = 300
OPENAI_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_OAUTH_ORIGINATOR = "openclaw"
OPENAI_OAUTH_USER_AGENT = "openclaw"
OPENAI_OAUTH_LOOPBACK_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_OAUTH_DEVICE_REDIRECT_URI = "https://auth.openai.com/deviceauth/callback"
OPENAI_OAUTH_DEVICE_VERIFICATION_URL = "https://auth.openai.com/codex/device"
OPENAI_OAUTH_DEVICE_USER_CODE_URL = (
    "https://auth.openai.com/api/accounts/deviceauth/usercode"
)
OPENAI_OAUTH_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
OPENAI_OAUTH_SCOPE = "openid profile email offline_access"
OPENAI_OAUTH_DEVICE_DEFAULT_POLL_INTERVAL_SECONDS = 5
OPENAI_OAUTH_DEVICE_MIN_POLL_INTERVAL_SECONDS = 1
OPENAI_OAUTH_PENDING_FLOW_PKCE = "pkce"
OPENAI_OAUTH_PENDING_FLOW_DEVICE_CODE = "device_code"


class OpenAIOAuthStateError(ValueError):
    """Raised when stored OpenAI OAuth state is malformed or unusable."""


@dataclass(frozen=True)
class OpenAIOAuthTokenState:
    """Persisted OAuth token state."""

    access_token: str
    refresh_token: str | None = None
    expires_at: str | None = None
    account_id: str | None = None
    last_refresh_at: str | None = None
    last_refresh_error: str | None = None


@dataclass(frozen=True)
class OpenAIOAuthPendingState:
    """Short-lived PKCE state for an in-progress OAuth connection."""

    state: str
    code_verifier: str
    redirect_uri: str
    created_at: str
    expires_at: str
    return_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenAIOAuthDevicePendingState:
    """Short-lived device-code state for an in-progress OAuth connection."""

    device_auth_id: str
    user_code: str
    verification_url: str
    poll_interval_seconds: int
    created_at: str
    expires_at: str
    last_poll_at: str | None = None


@dataclass(frozen=True)
class OpenAIOAuthStartResult:
    """Bootstrap data for an OAuth connection attempt."""

    auth_url: str
    state: str
    redirect_uri: str
    expires_at: str


@dataclass(frozen=True)
class OpenAIOAuthDeviceStartResult:
    """Bootstrap data for a device-code OAuth connection attempt."""

    verification_url: str
    user_code: str
    expires_at: str
    poll_interval_seconds: int


@dataclass(frozen=True)
class OpenAIOAuthStatus:
    """Sanitized OAuth connection status for API/UI surfaces."""

    connected: bool
    status: str
    has_refresh_token: bool = False
    account_id: str | None = None
    expires_at: str | None = None
    last_refresh_at: str | None = None
    last_refresh_error: str | None = None
    pending_expires_at: str | None = None
    pending_flow: str | None = None
    device_verification_url: str | None = None
    device_user_code: str | None = None
    device_poll_interval_seconds: int | None = None


@dataclass(frozen=True)
class OpenAIOAuthTokenResult:
    """Token exchange or refresh result returned by adapter implementations."""

    access_token: str
    refresh_token: str | None = None
    expires_at: str | None = None
    account_id: str | None = None


@dataclass(frozen=True)
class OpenAIOAuthDeviceCodeResult:
    """Device-code request result returned by adapter implementations."""

    device_auth_id: str
    user_code: str
    verification_url: str = OPENAI_OAUTH_DEVICE_VERIFICATION_URL
    poll_interval_seconds: int = OPENAI_OAUTH_DEVICE_DEFAULT_POLL_INTERVAL_SECONDS


@dataclass(frozen=True)
class OpenAIOAuthDevicePollResult:
    """Device-code poll result returned by adapter implementations."""

    status: Literal["pending", "authorized"]
    authorization_code: str | None = None
    code_verifier: str | None = None


class OpenAIOAuthTokenAdapter(Protocol):
    """Adapter boundary for real or fake token exchange and refresh."""

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> OpenAIOAuthTokenResult:
        """Exchange an authorization code for OAuth tokens."""

    async def refresh_token(self, *, refresh_token: str) -> OpenAIOAuthTokenResult:
        """Refresh an OAuth access token."""

    async def request_device_code(self) -> OpenAIOAuthDeviceCodeResult:
        """Request a device code for OpenAI OAuth."""

    async def poll_device_code(
        self,
        *,
        device_auth_id: str,
        user_code: str,
    ) -> OpenAIOAuthDevicePollResult:
        """Poll a device-code authorization attempt."""


@dataclass(frozen=True)
class StaticOpenAIOAuthTokenAdapter:
    """Deterministic token adapter for validation and smoke tests."""

    token_result: OpenAIOAuthTokenResult
    device_code_result: OpenAIOAuthDeviceCodeResult | None = None
    device_poll_result: OpenAIOAuthDevicePollResult | None = None

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> OpenAIOAuthTokenResult:
        """Return the configured token result."""

        return self.token_result

    async def refresh_token(self, *, refresh_token: str) -> OpenAIOAuthTokenResult:
        """Return the configured token result."""

        return self.token_result

    async def request_device_code(self) -> OpenAIOAuthDeviceCodeResult:
        """Return the configured device-code result."""

        if self.device_code_result is None:
            return OpenAIOAuthDeviceCodeResult(
                device_auth_id="validation-device-auth-id",
                user_code="VALIDATION-CODE",
            )
        return self.device_code_result

    async def poll_device_code(
        self,
        *,
        device_auth_id: str,
        user_code: str,
    ) -> OpenAIOAuthDevicePollResult:
        """Return the configured device poll result."""

        if self.device_poll_result is None:
            return OpenAIOAuthDevicePollResult(
                status="authorized",
                authorization_code="validation-device-authorization-code",
                code_verifier="validation-device-code-verifier",
            )
        return self.device_poll_result


@dataclass
class OpenAIOAuthHTTPTokenAdapter:
    """Codex-compatible HTTP adapter for OpenAI OAuth exchange and refresh."""

    token_url: str = OPENAI_OAUTH_TOKEN_URL
    client_id: str = OPENAI_OAUTH_CLIENT_ID
    timeout_seconds: float = 30.0
    http_client: httpx.AsyncClient | None = None

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> OpenAIOAuthTokenResult:
        """Exchange an authorization code for OpenAI OAuth tokens."""

        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        data = await self._post_form(payload)
        return _token_result_from_response(data)

    async def refresh_token(self, *, refresh_token: str) -> OpenAIOAuthTokenResult:
        """Refresh OpenAI OAuth tokens using the Codex-compatible form body."""

        payload = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        data = await self._post_form(payload)
        return _token_result_from_response(data)

    async def request_device_code(self) -> OpenAIOAuthDeviceCodeResult:
        """Request an OpenAI OAuth device code."""

        data = await self._post_json(
            OPENAI_OAUTH_DEVICE_USER_CODE_URL,
            {"client_id": self.client_id},
            pending_statuses=frozenset(),
        )
        if data is None:
            raise OpenAIOAuthStateError(
                "OpenAI OAuth device-code request returned pending status."
            )
        device_auth_id = _required_string(
            data,
            "device_auth_id",
            "OpenAI OAuth device-code response",
        )
        user_code = _optional_string(data, "user_code") or _optional_string(
            data,
            "usercode",
        )
        if user_code is None:
            raise OpenAIOAuthStateError(
                "OpenAI OAuth device-code response missing required field "
                "'user_code'."
            )
        return OpenAIOAuthDeviceCodeResult(
            device_auth_id=device_auth_id,
            user_code=user_code,
            verification_url=OPENAI_OAUTH_DEVICE_VERIFICATION_URL,
            poll_interval_seconds=_response_poll_interval_seconds(data),
        )

    async def poll_device_code(
        self,
        *,
        device_auth_id: str,
        user_code: str,
    ) -> OpenAIOAuthDevicePollResult:
        """Poll an OpenAI OAuth device-code authorization attempt."""

        data = await self._post_json(
            OPENAI_OAUTH_DEVICE_TOKEN_URL,
            {"device_auth_id": device_auth_id, "user_code": user_code},
            pending_statuses=frozenset({403, 404}),
        )
        if data is None:
            return OpenAIOAuthDevicePollResult(status="pending")
        return OpenAIOAuthDevicePollResult(
            status="authorized",
            authorization_code=_required_string(
                data,
                "authorization_code",
                "OpenAI OAuth device-code poll response",
            ),
            code_verifier=_required_string(
                data,
                "code_verifier",
                "OpenAI OAuth device-code poll response",
            ),
        )

    async def _post_form(self, payload: dict[str, str]) -> dict[str, Any]:
        data = await self._post(
            self.token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
        )
        if data is None:
            raise OpenAIOAuthStateError(
                "OpenAI OAuth token request returned pending status."
            )
        return data

    async def _post_json(
        self,
        url: str,
        payload: dict[str, str],
        *,
        pending_statuses: frozenset[int],
    ) -> dict[str, Any] | None:
        return await self._post(
            url,
            headers={"Content-Type": "application/json"},
            json_payload=payload,
            pending_statuses=pending_statuses,
        )

    async def _post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, str] | None = None,
        json_payload: dict[str, str] | None = None,
        pending_statuses: frozenset[int] = frozenset(),
    ) -> dict[str, Any] | None:
        client = self.http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            response = await client.post(
                url,
                headers={
                    **headers,
                    "originator": OPENAI_OAUTH_ORIGINATOR,
                    "User-Agent": OPENAI_OAUTH_USER_AGENT,
                },
                data=data,
                json=json_payload,
            )
        except httpx.HTTPError as exc:
            raise OpenAIOAuthStateError(
                f"OpenAI OAuth token request failed: {type(exc).__name__}."
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code in pending_statuses:
            return None
        if response.status_code < 200 or response.status_code >= 300:
            private_values = [
                value
                for key, value in {**(data or {}), **(json_payload or {})}.items()
                if "token" in key or "secret" in key or key in {"code", "device_code"}
            ]
            details = provider_response_error_details(
                response.status_code, response.text, redactions=private_values
            )
            raise OpenAIOAuthStateError(
                f"OpenAI OAuth token request failed: {details['error']}"
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise OpenAIOAuthStateError(
                "OpenAI OAuth token response was not valid JSON."
            ) from exc
        if not isinstance(payload, dict):
            raise OpenAIOAuthStateError(
                "OpenAI OAuth token response must be a JSON object."
            )
        return payload


_token_adapter: OpenAIOAuthTokenAdapter | None = None


def set_openai_oauth_token_adapter(adapter: OpenAIOAuthTokenAdapter | None) -> None:
    """Set the process-local token adapter used by OAuth completion."""

    global _token_adapter
    _token_adapter = adapter


def get_openai_oauth_token_adapter() -> OpenAIOAuthTokenAdapter:
    """Return the configured token adapter, defaulting to the real HTTP adapter."""

    if _token_adapter is None:
        return OpenAIOAuthHTTPTokenAdapter()
    return _token_adapter


def is_openai_oauth_internal_secret(name: str) -> bool:
    """Return True when a secret name belongs to internal OpenAI OAuth state."""

    return name in OPENAI_OAUTH_INTERNAL_SECRETS


def start_openai_oauth(
    *,
    redirect_uri: str,
    now: datetime | None = None,
) -> OpenAIOAuthStartResult:
    """Create pending PKCE state and return OAuth bootstrap data."""

    reference = _normalize_datetime(now or datetime.now(UTC))
    expires_at = reference + timedelta(seconds=OPENAI_OAUTH_PENDING_TTL_SECONDS)
    state = secrets.token_hex(16)
    code_verifier = secrets.token_urlsafe(32)
    pending = OpenAIOAuthPendingState(
        state=state,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        created_at=reference.isoformat(),
        expires_at=expires_at.isoformat(),
    )
    save_openai_oauth_pending_state(pending)

    query = urlencode(
        {
            "response_type": "code",
            "client_id": OPENAI_OAUTH_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": OPENAI_OAUTH_SCOPE,
            "code_challenge": _pkce_challenge(code_verifier),
            "code_challenge_method": "S256",
            "state": state,
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": OPENAI_OAUTH_ORIGINATOR,
        }
    )
    return OpenAIOAuthStartResult(
        auth_url=f"{OPENAI_OAUTH_AUTHORIZE_URL}?{query}",
        state=state,
        redirect_uri=redirect_uri,
        expires_at=expires_at.isoformat(),
    )


async def start_openai_oauth_device_code(
    *,
    adapter: OpenAIOAuthTokenAdapter | None = None,
    now: datetime | None = None,
) -> OpenAIOAuthDeviceStartResult:
    """Create pending device-code state and return user-facing bootstrap data."""

    reference = _normalize_datetime(now or datetime.now(UTC))
    token_adapter = adapter or get_openai_oauth_token_adapter()
    result = await token_adapter.request_device_code()
    expires_at = reference + timedelta(seconds=OPENAI_OAUTH_DEVICE_PENDING_TTL_SECONDS)
    poll_interval = _normalize_poll_interval_seconds(result.poll_interval_seconds)
    pending = OpenAIOAuthDevicePendingState(
        device_auth_id=result.device_auth_id,
        user_code=result.user_code,
        verification_url=result.verification_url,
        poll_interval_seconds=poll_interval,
        created_at=reference.isoformat(),
        expires_at=expires_at.isoformat(),
    )
    save_openai_oauth_device_pending_state(pending)
    return OpenAIOAuthDeviceStartResult(
        verification_url=result.verification_url,
        user_code=result.user_code,
        expires_at=expires_at.isoformat(),
        poll_interval_seconds=poll_interval,
    )


async def complete_openai_oauth(
    *,
    code: str,
    state: str,
    adapter: OpenAIOAuthTokenAdapter | None = None,
    now: datetime | None = None,
) -> OpenAIOAuthTokenState:
    """Consume pending PKCE state and persist exchanged OAuth tokens."""

    pending = consume_openai_oauth_pending_state(state=state, now=now)
    token_adapter = adapter or get_openai_oauth_token_adapter()
    token_result = await token_adapter.exchange_code(
        code=code,
        code_verifier=pending.code_verifier,
        redirect_uri=pending.redirect_uri,
    )
    token_state = token_result_to_state(token_result)
    save_openai_oauth_token_state(token_state)
    return token_state


async def poll_openai_oauth_device_code(
    *,
    adapter: OpenAIOAuthTokenAdapter | None = None,
    now: datetime | None = None,
) -> OpenAIOAuthTokenState | None:
    """Poll pending device-code state and persist tokens once authorized."""

    reference = _normalize_datetime(now or datetime.now(UTC))
    pending = load_openai_oauth_device_pending_state(
        now=reference,
        cleanup_expired=True,
    )
    if pending is None:
        raise OpenAIOAuthStateError("No active OpenAI OAuth device-code attempt.")

    token_adapter = adapter or get_openai_oauth_token_adapter()
    result = await token_adapter.poll_device_code(
        device_auth_id=pending.device_auth_id,
        user_code=pending.user_code,
    )
    if result.status == "pending":
        save_openai_oauth_device_pending_state(
            OpenAIOAuthDevicePendingState(
                device_auth_id=pending.device_auth_id,
                user_code=pending.user_code,
                verification_url=pending.verification_url,
                poll_interval_seconds=pending.poll_interval_seconds,
                created_at=pending.created_at,
                expires_at=pending.expires_at,
                last_poll_at=reference.isoformat(),
            )
        )
        return None

    if not result.authorization_code or not result.code_verifier:
        raise OpenAIOAuthStateError(
            "OpenAI OAuth device-code poll response was authorized but incomplete."
        )
    token_result = await token_adapter.exchange_code(
        code=result.authorization_code,
        code_verifier=result.code_verifier,
        redirect_uri=OPENAI_OAUTH_DEVICE_REDIRECT_URI,
    )
    token_state = token_result_to_state(token_result)
    save_openai_oauth_token_state(token_state)
    clear_openai_oauth_pending_state()
    return token_state


async def complete_openai_oauth_from_redirect(
    *,
    redirect_url: str | None = None,
    code: str | None = None,
    state: str | None = None,
    adapter: OpenAIOAuthTokenAdapter | None = None,
    now: datetime | None = None,
) -> OpenAIOAuthTokenState:
    """Complete OAuth from a pasted redirect URL or raw code/state pair."""

    resolved_code = code
    resolved_state = state
    if redirect_url:
        parsed_code, parsed_state = _parse_redirect_values(redirect_url)
        resolved_code = parsed_code
        resolved_state = parsed_state

    if not resolved_code:
        raise OpenAIOAuthStateError("OpenAI OAuth completion requires a code.")

    if not resolved_state:
        pending = load_openai_oauth_pending_state(now=now, cleanup_expired=True)
        if pending is None:
            raise OpenAIOAuthStateError(
                "OpenAI OAuth completion requires state when no pending attempt exists."
            )
        resolved_state = pending.state

    return await complete_openai_oauth(
        code=resolved_code,
        state=resolved_state,
        adapter=adapter,
        now=now,
    )


async def ensure_fresh_openai_oauth_token(
    *,
    adapter: OpenAIOAuthTokenAdapter | None = None,
    now: datetime | None = None,
) -> OpenAIOAuthTokenState:
    """Return a stored OAuth token, refreshing it when expiry is near."""

    token_state = load_openai_oauth_token_state()
    if token_state is None:
        raise OpenAIOAuthStateError("OpenAI OAuth is not connected.")

    if not _token_needs_refresh(token_state, now=now):
        return token_state

    if not token_state.refresh_token:
        if _is_token_expired(token_state, now=now):
            raise OpenAIOAuthStateError(
                "OpenAI OAuth access token expired and no refresh token is stored."
            )
        return token_state

    reference = _normalize_datetime(now or datetime.now(UTC))
    token_adapter = adapter or get_openai_oauth_token_adapter()
    try:
        token_result = await token_adapter.refresh_token(
            refresh_token=token_state.refresh_token
        )
    except OpenAIOAuthStateError as exc:
        failed_state = token_result_to_state(
            OpenAIOAuthTokenResult(
                access_token=token_state.access_token,
                refresh_token=token_state.refresh_token,
                expires_at=token_state.expires_at,
                account_id=token_state.account_id,
            ),
            previous=token_state,
            refreshed_at=reference.isoformat(),
            refresh_error=str(exc),
        )
        save_openai_oauth_token_state(failed_state)
        raise

    refreshed_state = token_result_to_state(
        token_result,
        previous=token_state,
        refreshed_at=reference.isoformat(),
        refresh_error=None,
    )
    save_openai_oauth_token_state(refreshed_state)
    return refreshed_state


def save_openai_oauth_token_state(state: OpenAIOAuthTokenState) -> None:
    """Persist OpenAI OAuth token state as an internal secret."""

    if not state.access_token.strip():
        raise OpenAIOAuthStateError("OpenAI OAuth access token cannot be empty.")
    set_secret_value(
        OPENAI_OAUTH_TOKEN_SECRET,
        json.dumps(_token_state_to_dict(state), separators=(",", ":")),
    )


def load_openai_oauth_token_state() -> OpenAIOAuthTokenState | None:
    """Load stored OpenAI OAuth token state, if present."""

    payload = get_secret_value(OPENAI_OAUTH_TOKEN_SECRET)
    if not payload:
        return None
    raw = _load_json_mapping(payload, "OpenAI OAuth token state")
    access_token = _required_string(raw, "access_token", "OpenAI OAuth token state")
    return OpenAIOAuthTokenState(
        access_token=access_token,
        refresh_token=_optional_string(raw, "refresh_token"),
        expires_at=_optional_string(raw, "expires_at"),
        account_id=_optional_string(raw, "account_id"),
        last_refresh_at=_optional_string(raw, "last_refresh_at"),
        last_refresh_error=_optional_string(raw, "last_refresh_error"),
    )


def clear_openai_oauth_token_state() -> None:
    """Delete stored OpenAI OAuth token state."""

    delete_secret(OPENAI_OAUTH_TOKEN_SECRET)


def save_openai_oauth_pending_state(state: OpenAIOAuthPendingState) -> None:
    """Persist one pending OpenAI OAuth connection attempt."""

    _parse_datetime(state.expires_at, "expires_at")
    set_secret_value(
        OPENAI_OAUTH_PENDING_SECRET,
        json.dumps(_pending_state_to_dict(state), separators=(",", ":")),
    )


def save_openai_oauth_device_pending_state(
    state: OpenAIOAuthDevicePendingState,
) -> None:
    """Persist one pending OpenAI OAuth device-code connection attempt."""

    _parse_datetime(state.expires_at, "expires_at")
    set_secret_value(
        OPENAI_OAUTH_PENDING_SECRET,
        json.dumps(_device_pending_state_to_dict(state), separators=(",", ":")),
    )


def load_openai_oauth_pending_state(
    *,
    now: datetime | None = None,
    cleanup_expired: bool = True,
) -> OpenAIOAuthPendingState | None:
    """Load pending OAuth state, deleting it when expired by lazy TTL cleanup."""

    payload = get_secret_value(OPENAI_OAUTH_PENDING_SECRET)
    if not payload:
        return None
    if _pending_flow_from_payload(payload) != OPENAI_OAUTH_PENDING_FLOW_PKCE:
        return None
    pending = _pending_state_from_payload(payload)
    if _is_expired(pending.expires_at, now=now):
        if cleanup_expired:
            clear_openai_oauth_pending_state()
        return None
    return pending


def load_openai_oauth_device_pending_state(
    *,
    now: datetime | None = None,
    cleanup_expired: bool = True,
) -> OpenAIOAuthDevicePendingState | None:
    """Load pending device-code state, deleting it when expired."""

    payload = get_secret_value(OPENAI_OAUTH_PENDING_SECRET)
    if not payload:
        return None
    if _pending_flow_from_payload(payload) != OPENAI_OAUTH_PENDING_FLOW_DEVICE_CODE:
        return None
    pending = _device_pending_state_from_payload(payload)
    if _is_expired(pending.expires_at, now=now):
        if cleanup_expired:
            clear_openai_oauth_pending_state()
        return None
    return pending


def consume_openai_oauth_pending_state(
    *,
    state: str,
    now: datetime | None = None,
) -> OpenAIOAuthPendingState:
    """Return and delete matching pending state, enforcing TTL and single use."""

    pending = load_openai_oauth_pending_state(now=now, cleanup_expired=True)
    if pending is None:
        raise OpenAIOAuthStateError("No active OpenAI OAuth connection attempt.")
    if pending.state != state:
        raise OpenAIOAuthStateError("OpenAI OAuth state did not match.")
    clear_openai_oauth_pending_state()
    return pending


def clear_openai_oauth_pending_state() -> None:
    """Delete stored pending OAuth state."""

    delete_secret(OPENAI_OAUTH_PENDING_SECRET)


def clear_openai_oauth_state() -> None:
    """Delete all internal OpenAI OAuth token and pending state."""

    clear_openai_oauth_token_state()
    clear_openai_oauth_pending_state()


def get_openai_oauth_status(now: datetime | None = None) -> OpenAIOAuthStatus:
    """Return sanitized OpenAI OAuth status."""

    token_state = load_openai_oauth_token_state()
    pending_state = load_openai_oauth_pending_state(now=now, cleanup_expired=True)
    device_pending_state = load_openai_oauth_device_pending_state(
        now=now,
        cleanup_expired=True,
    )

    if token_state is not None:
        status = "refresh_failed" if token_state.last_refresh_error else "connected"
        pending_expires_at = None
        pending_flow = None
        device_verification_url = None
        device_user_code = None
        device_poll_interval_seconds = None
        if pending_state is not None:
            pending_expires_at = pending_state.expires_at
            pending_flow = OPENAI_OAUTH_PENDING_FLOW_PKCE
        elif device_pending_state is not None:
            pending_expires_at = device_pending_state.expires_at
            pending_flow = OPENAI_OAUTH_PENDING_FLOW_DEVICE_CODE
            device_verification_url = device_pending_state.verification_url
            device_user_code = device_pending_state.user_code
            device_poll_interval_seconds = device_pending_state.poll_interval_seconds
        return OpenAIOAuthStatus(
            connected=True,
            status=status,
            has_refresh_token=bool(token_state.refresh_token),
            account_id=token_state.account_id,
            expires_at=token_state.expires_at,
            last_refresh_at=token_state.last_refresh_at,
            last_refresh_error=token_state.last_refresh_error,
            pending_expires_at=pending_expires_at,
            pending_flow=pending_flow,
            device_verification_url=device_verification_url,
            device_user_code=device_user_code,
            device_poll_interval_seconds=device_poll_interval_seconds,
        )

    if pending_state is not None:
        return OpenAIOAuthStatus(
            connected=False,
            status="pending",
            pending_expires_at=pending_state.expires_at,
            pending_flow=OPENAI_OAUTH_PENDING_FLOW_PKCE,
        )

    if device_pending_state is not None:
        return OpenAIOAuthStatus(
            connected=False,
            status="pending",
            pending_expires_at=device_pending_state.expires_at,
            pending_flow=OPENAI_OAUTH_PENDING_FLOW_DEVICE_CODE,
            device_verification_url=device_pending_state.verification_url,
            device_user_code=device_pending_state.user_code,
            device_poll_interval_seconds=device_pending_state.poll_interval_seconds,
        )

    return OpenAIOAuthStatus(connected=False, status="disconnected")


def token_result_to_state(
    result: OpenAIOAuthTokenResult,
    *,
    previous: OpenAIOAuthTokenState | None = None,
    refreshed_at: str | None = None,
    refresh_error: str | None = None,
) -> OpenAIOAuthTokenState:
    """Convert an adapter token result into persisted state."""

    return OpenAIOAuthTokenState(
        access_token=result.access_token,
        refresh_token=(
            result.refresh_token
            if result.refresh_token is not None
            else previous.refresh_token if previous is not None else None
        ),
        expires_at=result.expires_at,
        account_id=(
            result.account_id
            if result.account_id is not None
            else previous.account_id if previous is not None else None
        ),
        last_refresh_at=refreshed_at,
        last_refresh_error=refresh_error,
    )


def _token_state_to_dict(state: OpenAIOAuthTokenState) -> dict[str, Any]:
    return {
        "access_token": state.access_token,
        "refresh_token": state.refresh_token,
        "expires_at": state.expires_at,
        "account_id": state.account_id,
        "last_refresh_at": state.last_refresh_at,
        "last_refresh_error": state.last_refresh_error,
    }


def _pending_state_to_dict(state: OpenAIOAuthPendingState) -> dict[str, Any]:
    return {
        "flow": OPENAI_OAUTH_PENDING_FLOW_PKCE,
        "state": state.state,
        "code_verifier": state.code_verifier,
        "redirect_uri": state.redirect_uri,
        "created_at": state.created_at,
        "expires_at": state.expires_at,
        "return_metadata": state.return_metadata,
    }


def _device_pending_state_to_dict(
    state: OpenAIOAuthDevicePendingState,
) -> dict[str, Any]:
    return {
        "flow": OPENAI_OAUTH_PENDING_FLOW_DEVICE_CODE,
        "device_auth_id": state.device_auth_id,
        "user_code": state.user_code,
        "verification_url": state.verification_url,
        "poll_interval_seconds": state.poll_interval_seconds,
        "created_at": state.created_at,
        "expires_at": state.expires_at,
        "last_poll_at": state.last_poll_at,
    }


def _pending_flow_from_payload(payload: str) -> str:
    raw = _load_json_mapping(payload, "OpenAI OAuth pending state")
    flow = raw.get("flow")
    if isinstance(flow, str) and flow.strip():
        return flow.strip()
    if "device_auth_id" in raw:
        return OPENAI_OAUTH_PENDING_FLOW_DEVICE_CODE
    return OPENAI_OAUTH_PENDING_FLOW_PKCE


def _pending_state_from_payload(payload: str) -> OpenAIOAuthPendingState:
    raw = _load_json_mapping(payload, "OpenAI OAuth pending state")
    return_metadata = raw.get("return_metadata", {})
    if not isinstance(return_metadata, dict):
        return_metadata = {}
    return OpenAIOAuthPendingState(
        state=_required_string(raw, "state", "OpenAI OAuth pending state"),
        code_verifier=_required_string(
            raw, "code_verifier", "OpenAI OAuth pending state"
        ),
        redirect_uri=_required_string(
            raw, "redirect_uri", "OpenAI OAuth pending state"
        ),
        created_at=_required_string(raw, "created_at", "OpenAI OAuth pending state"),
        expires_at=_required_string(raw, "expires_at", "OpenAI OAuth pending state"),
        return_metadata=return_metadata,
    )


def _device_pending_state_from_payload(payload: str) -> OpenAIOAuthDevicePendingState:
    raw = _load_json_mapping(payload, "OpenAI OAuth device-code pending state")
    return OpenAIOAuthDevicePendingState(
        device_auth_id=_required_string(
            raw,
            "device_auth_id",
            "OpenAI OAuth device-code pending state",
        ),
        user_code=_required_string(
            raw,
            "user_code",
            "OpenAI OAuth device-code pending state",
        ),
        verification_url=_required_string(
            raw,
            "verification_url",
            "OpenAI OAuth device-code pending state",
        ),
        poll_interval_seconds=_normalize_poll_interval_seconds(
            raw.get("poll_interval_seconds")
        ),
        created_at=_required_string(
            raw,
            "created_at",
            "OpenAI OAuth device-code pending state",
        ),
        expires_at=_required_string(
            raw,
            "expires_at",
            "OpenAI OAuth device-code pending state",
        ),
        last_poll_at=_optional_string(raw, "last_poll_at"),
    )


def _load_json_mapping(payload: str, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OpenAIOAuthStateError(f"{label} is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise OpenAIOAuthStateError(f"{label} must be a JSON object.")
    return raw


def _token_result_from_response(payload: dict[str, Any]) -> OpenAIOAuthTokenResult:
    access_token = _required_string(
        payload, "access_token", "OpenAI OAuth token response"
    )
    id_token = _optional_string(payload, "id_token")
    refresh_token = _optional_string(payload, "refresh_token")
    expires_at = _response_expires_at(
        payload, access_token=access_token, id_token=id_token
    )
    account_id = _response_account_id(
        payload,
        access_token=access_token,
        id_token=id_token,
    )
    return OpenAIOAuthTokenResult(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        account_id=account_id,
    )


def _response_poll_interval_seconds(payload: dict[str, Any]) -> int:
    interval = payload.get("interval")
    return _normalize_poll_interval_seconds(interval)


def _normalize_poll_interval_seconds(value: object) -> int:
    if isinstance(value, int | float):
        seconds = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        seconds = int(value.strip())
    else:
        seconds = OPENAI_OAUTH_DEVICE_DEFAULT_POLL_INTERVAL_SECONDS
    return max(seconds, OPENAI_OAUTH_DEVICE_MIN_POLL_INTERVAL_SECONDS)


def _response_expires_at(
    payload: dict[str, Any],
    *,
    access_token: str,
    id_token: str | None,
) -> str | None:
    expires_at = _optional_string(payload, "expires_at")
    if expires_at:
        return expires_at

    expires_in = payload.get("expires_in")
    if isinstance(expires_in, int | float) and expires_in > 0:
        return (datetime.now(UTC) + timedelta(seconds=float(expires_in))).isoformat()

    for token in (id_token, access_token):
        exp = _jwt_expiration(token)
        if exp is not None:
            return exp.isoformat()
    return None


def _response_account_id(
    payload: dict[str, Any],
    *,
    access_token: str,
    id_token: str | None,
) -> str | None:
    explicit = _optional_string(payload, "account_id")
    if explicit:
        return explicit
    for token in (access_token, id_token):
        claims = _jwt_claims(token)
        auth_claims = claims.get("https://api.openai.com/auth")
        if isinstance(auth_claims, dict):
            account_id = auth_claims.get("chatgpt_account_id")
            if isinstance(account_id, str) and account_id.strip():
                return account_id.strip()
    return None


def _required_string(raw: dict[str, Any], key: str, label: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OpenAIOAuthStateError(f"{label} missing required field '{key}'.")
    return value


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _jwt_expiration(token: str | None) -> datetime | None:
    claims = _jwt_claims(token)
    exp = claims.get("exp")
    if isinstance(exp, int | float):
        return datetime.fromtimestamp(exp, tz=UTC)
    return None


def _jwt_claims(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return {}
    payload = parts[1]
    padding = "=" * (-len(payload) % 4)
    try:
        decoded = base64.urlsafe_b64decode(f"{payload}{padding}")
        claims = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _is_expired(expires_at: str, *, now: datetime | None = None) -> bool:
    expiry = _parse_datetime(expires_at, "expires_at")
    reference = _normalize_datetime(now or datetime.now(UTC))
    return expiry <= reference


def _is_token_expired(
    token_state: OpenAIOAuthTokenState,
    *,
    now: datetime | None = None,
) -> bool:
    if not token_state.expires_at:
        return False
    return _is_expired(token_state.expires_at, now=now)


def _token_needs_refresh(
    token_state: OpenAIOAuthTokenState,
    *,
    now: datetime | None = None,
) -> bool:
    if not token_state.expires_at:
        return False
    expiry = _parse_datetime(token_state.expires_at, "expires_at")
    reference = _normalize_datetime(now or datetime.now(UTC))
    refresh_at = expiry - timedelta(seconds=OPENAI_OAUTH_REFRESH_WINDOW_SECONDS)
    return refresh_at <= reference


def _parse_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OpenAIOAuthStateError(
            f"OpenAI OAuth {field_name} must be an ISO 8601 timestamp."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _pkce_challenge(code_verifier: str) -> str:
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _parse_redirect_values(redirect_url: str) -> tuple[str | None, str | None]:
    parsed = urlparse(redirect_url)
    query = parse_qs(parsed.query)
    code = _single_query_value(query, "code")
    state = _single_query_value(query, "state")
    return code, state


def _single_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key) or []
    if not values:
        return None
    value = values[0].strip()
    return value or None
