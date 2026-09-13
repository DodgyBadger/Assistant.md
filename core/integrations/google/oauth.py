"""Headless-safe Google OAuth authorization and refresh coordination."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from core.connections import BuiltInConnectionService, GoogleConnection
from core.identity import ExecutionAuthority
from core.logger import UnifiedLogger
from core.oauth import (
    EncryptedOAuthStorage,
    OAuthHTTPClientFactory,
    OAuthPKCEState,
    OAuthTokenExchangeError,
    request_oauth_token,
    validate_redirect_uri,
)
from core.secrets import EncryptedSecretsService, SecretGuardMismatchError
from core.settings import get_default_api_timeout

from .connection import (
    GOOGLE_OAUTH_NAMESPACE,
    GOOGLE_OAUTH_PENDING_KEY,
    GoogleCapability,
    GoogleConnectionService,
    GoogleCredentialChangedError,
    GoogleOAuthClientCredential,
    GoogleOAuthStateChangedError,
    GoogleOAuthTokenState,
)

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GOOGLE_OAUTH_PENDING_SECONDS = 10 * 60
_OAUTH_COLLECTION = "google"

logger = UnifiedLogger(tag="google-connections")


class GoogleOAuthError(ValueError):
    """Raised for sanitized, user-correctable Google OAuth failures."""


@dataclass(frozen=True)
class GoogleOAuthStart:
    """Sanitized pending Google authorization details."""

    authorization_url: str
    redirect_uri: str
    expires_at: str
    requested_scopes: tuple[str, ...]


@dataclass(frozen=True)
class _PendingAuthorization:
    payload: dict[str, object]
    expires_at: float | None
    credential: GoogleOAuthClientCredential


class GoogleOAuthCoordinator:
    """Coordinate principal-owned Google authorization and token refresh."""

    def __init__(
        self,
        *,
        connections: BuiltInConnectionService,
        google: GoogleConnectionService,
        secrets: EncryptedSecretsService,
        http_client_factory: OAuthHTTPClientFactory | None = None,
    ) -> None:
        self._connections = connections
        self._google = google
        self._secrets = secrets
        self._http_client_factory = http_client_factory or _google_http_client
        self._refresh_locks: dict[str, asyncio.Lock] = {}

    def start(
        self,
        *,
        authority: ExecutionAuthority,
        redirect_uri: str,
        capabilities: tuple[GoogleCapability, ...],
        connection_id: str | None = None,
    ) -> GoogleOAuthStart:
        """Create and durably store one authorization-code/PKCE attempt."""
        connection = self._connections.get_google_connection_for_authority(
            authority, connection_id
        )
        if connection is None:
            raise GoogleOAuthError("Save Google connection configuration first.")
        credential = self._google.resolve_client_credential(
            authority, connection.connection_id
        )
        if credential is None:
            raise GoogleOAuthError("Save the Google OAuth client secret first.")
        try:
            clean_redirect = validate_redirect_uri(redirect_uri)
        except ValueError as exc:
            raise GoogleOAuthError("Google OAuth redirect URI is invalid.") from exc
        existing = self._google.load_token_state(authority, connection.connection_id)
        requested = set(existing.scopes if existing is not None else ())
        for capability in capabilities:
            requested.update(capability.required_scopes)
        requested_scopes = tuple(sorted(requested))
        pkce = OAuthPKCEState.generate()
        expires_at = datetime.now(UTC) + timedelta(seconds=GOOGLE_OAUTH_PENDING_SECONDS)
        pending_payload = {
            "state": pkce.state,
            "code_verifier": pkce.code_verifier,
            "redirect_uri": clean_redirect,
            "requested_scopes": list(requested_scopes),
            "existing_account_id": (
                existing.account_id if existing is not None else None
            ),
            "oauth_generation": credential.oauth_generation,
            "credential_id": credential.credential_id,
            "expires_at": expires_at.isoformat(),
        }
        storage = self._storage(authority, connection.connection_id)
        try:
            stored_expires_at = storage.put_sync_if_unchanged(
                GOOGLE_OAUTH_PENDING_KEY,
                pending_payload,
                guard_key="client-secret",
                expected_guard_value={
                    "value": credential.value,
                    "oauth_generation": credential.oauth_generation,
                    "credential_id": credential.credential_id,
                },
                collection=_OAUTH_COLLECTION,
                guard_collection=_OAUTH_COLLECTION,
                ttl=GOOGLE_OAUTH_PENDING_SECONDS,
            )
        except SecretGuardMismatchError as exc:
            raise GoogleOAuthError(
                "Google OAuth client credential changed while authorization started."
            ) from exc
        try:
            storage.put_sync_if_unchanged(
                GOOGLE_OAUTH_PENDING_KEY,
                pending_payload,
                guard_key="client-secret",
                expected_guard_value={
                    "value": credential.value,
                    "oauth_generation": credential.oauth_generation,
                    "credential_id": credential.credential_id,
                },
                collection=_OAUTH_COLLECTION,
                guard_collection=_OAUTH_COLLECTION,
                additional_guard_key=GOOGLE_OAUTH_PENDING_KEY,
                additional_expected_guard_value=pending_payload,
                additional_expected_guard_expires_at=stored_expires_at,
                expires_at=stored_expires_at,
            )
            current = self._connections.get_google_connection_for_authority(
                authority, connection.connection_id
            )
            if (
                current is None
                or current.oauth_generation != connection.oauth_generation
            ):
                raise SecretGuardMismatchError(
                    "Google connection metadata changed during OAuth start."
                )
        except SecretGuardMismatchError as exc:
            try:
                storage.delete_sync_if_unchanged(
                    GOOGLE_OAUTH_PENDING_KEY,
                    pending_payload,
                    collection=_OAUTH_COLLECTION,
                    expires_at=stored_expires_at,
                )
            except SecretGuardMismatchError:
                pass
            raise GoogleOAuthError(
                "Google connection or authorization state changed while authorization started."
            ) from exc
        params = {
            "response_type": "code",
            "client_id": connection.client_id,
            "redirect_uri": clean_redirect,
            "scope": " ".join(requested_scopes),
            "state": pkce.state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": pkce.code_challenge_method,
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
        return GoogleOAuthStart(
            authorization_url=f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}",
            redirect_uri=clean_redirect,
            expires_at=expires_at.isoformat(),
            requested_scopes=requested_scopes,
        )

    async def complete(
        self,
        *,
        authority: ExecutionAuthority,
        code: str,
        state: str,
        connection_id: str | None = None,
    ) -> GoogleOAuthTokenState:
        """Exchange one persisted attempt and verify the connected identity."""
        connection = (
            self._connection_for_pending_state(authority, state)
            if connection_id is None
            else self._connections.get_google_connection_for_authority(
                authority, connection_id
            )
        )
        if connection is None:
            raise GoogleOAuthError("Google connection was not found.")
        pending = self._load_pending(authority, connection.connection_id)
        if pending is None:
            raise GoogleOAuthError("No active Google authorization attempt was found.")
        expected = str(pending.payload["state"])
        if not OAuthPKCEState(
            state=expected,
            code_verifier=str(pending.payload["code_verifier"]),
            code_challenge="unused",
        ).matches_state(state):
            raise GoogleOAuthError("Google OAuth state did not match.")
        try:
            self._storage(authority, connection.connection_id).delete_sync_if_unchanged(
                GOOGLE_OAUTH_PENDING_KEY,
                pending.payload,
                collection=_OAUTH_COLLECTION,
                expires_at=pending.expires_at,
            )
        except SecretGuardMismatchError as exc:
            raise GoogleOAuthError(
                "Google authorization attempt changed. Start authorization again."
            ) from exc
        try:
            credential = self._google.resolve_client_credential(
                authority, connection.connection_id
            )
            if credential != pending.credential:
                raise GoogleCredentialChangedError(
                    "Google OAuth client credential changed during authorization."
                )
            token = await request_oauth_token(
                token_endpoint=GOOGLE_TOKEN_ENDPOINT,
                form={
                    "grant_type": "authorization_code",
                    "code": str(code or ""),
                    "client_id": connection.client_id,
                    "client_secret": credential.value,
                    "redirect_uri": str(pending.payload["redirect_uri"]),
                    "code_verifier": str(pending.payload["code_verifier"]),
                },
                http_client_factory=self._http_client_factory,
            )
            identity = await self._load_identity(token.access_token)
            account_id = _required_identity(identity, "sub")
            account_email = _required_identity(identity, "email")
            existing_account_id = pending.payload.get("existing_account_id")
            if existing_account_id and account_id != existing_account_id:
                raise GoogleOAuthError(
                    "Google authorization returned a different account. "
                    "Disconnect the existing account before replacing it."
                )
            existing = self._google.load_token_state(
                authority, connection.connection_id
            )
            refresh_token = token.refresh_token or (
                existing.refresh_token if existing is not None else None
            )
            if refresh_token is None:
                raise GoogleOAuthError(
                    "Google did not return an offline refresh token. Start authorization again."
                )
            requested_scopes = _string_tuple(pending.payload.get("requested_scopes"))
            scopes = token.scopes or requested_scopes
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=token.expires_in)
                if token.expires_in is not None
                else None
            )
            result = GoogleOAuthTokenState(
                access_token=token.access_token,
                refresh_token=refresh_token,
                expires_at=expires_at.isoformat() if expires_at is not None else None,
                scopes=scopes,
                token_type=token.token_type,
                account_id=account_id,
                account_email=account_email,
            )
            self._google.save_token_state(
                authority,
                result,
                connection.connection_id,
                expected_credential=credential,
            )
            return result
        except OAuthTokenExchangeError as exc:
            raise GoogleOAuthError(
                "Google rejected OAuth completion. Start authorization again."
            ) from exc
        except (GoogleCredentialChangedError, GoogleOAuthStateChangedError) as exc:
            raise GoogleOAuthError(
                "Google OAuth client configuration changed. Start authorization again."
            ) from exc

    def _connection_for_pending_state(
        self, authority: ExecutionAuthority, state: str
    ) -> GoogleConnection | None:
        """Resolve an installation callback to its principal-owned attempt."""
        for connection in self._connections.list_google_connections_for_authority(
            authority
        ):
            pending = self._load_pending(authority, connection.connection_id)
            if pending is None:
                continue
            expected = str(pending.payload.get("state") or "")
            if expected and OAuthPKCEState(
                state=expected,
                code_verifier="unused",
                code_challenge="unused",
            ).matches_state(state):
                return connection
        return None

    async def refresh(
        self, authority: ExecutionAuthority, connection_id: str | None = None
    ) -> GoogleOAuthTokenState:
        """Refresh one Google grant under a per-principal serialization lock."""
        connection = self._require_connection(authority, connection_id)
        lock = self._refresh_locks.setdefault(
            f"{authority.principal_id}:{connection.connection_id}", asyncio.Lock()
        )
        async with lock:
            return await self._refresh_locked(authority, connection.connection_id)

    async def access_token(
        self, authority: ExecutionAuthority, connection_id: str | None = None
    ) -> str:
        """Resolve a usable access token, refreshing expired grants once."""
        connection = self._require_connection(authority, connection_id)
        lock = self._refresh_locks.setdefault(
            f"{authority.principal_id}:{connection.connection_id}", asyncio.Lock()
        )
        async with lock:
            existing = self._google.load_token_state(
                authority, connection.connection_id
            )
            if existing is None:
                raise GoogleOAuthError("Google authorization must be connected.")
            if existing.expired:
                existing = await self._refresh_locked(
                    authority, connection.connection_id
                )
            return existing.access_token

    async def _refresh_locked(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> GoogleOAuthTokenState:
        """Refresh while the caller holds the principal's serialization lock."""
        connection = self._connections.get_google_connection_for_authority(
            authority, connection_id
        )
        credential = self._google.resolve_client_credential(authority, connection_id)
        existing = self._google.load_token_state(authority, connection_id)
        if (
            existing is None
            or not existing.refresh_token
            or connection is None
            or credential is None
        ):
            raise GoogleOAuthError("Google authorization must be reconnected.")
        try:
            token = await request_oauth_token(
                token_endpoint=GOOGLE_TOKEN_ENDPOINT,
                form={
                    "grant_type": "refresh_token",
                    "refresh_token": existing.refresh_token,
                    "client_id": connection.client_id,
                    "client_secret": credential.value,
                },
                http_client_factory=self._http_client_factory,
            )
        except OAuthTokenExchangeError as exc:
            raise GoogleOAuthError("Google token refresh failed.") from exc
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=token.expires_in)
            if token.expires_in is not None
            else None
        )
        refreshed = GoogleOAuthTokenState(
            access_token=token.access_token,
            refresh_token=token.refresh_token or existing.refresh_token,
            expires_at=expires_at.isoformat() if expires_at is not None else None,
            scopes=token.scopes or existing.scopes,
            token_type=token.token_type,
            account_id=existing.account_id,
            account_email=existing.account_email,
        )
        try:
            self._google.save_token_state(
                authority,
                refreshed,
                connection_id,
                expected_credential=credential,
                expected_token_state=existing,
            )
        except (GoogleCredentialChangedError, GoogleOAuthStateChangedError) as exc:
            raise GoogleOAuthError(
                "Google OAuth client configuration changed. Reconnect Google."
            ) from exc
        return refreshed

    async def _load_identity(self, access_token: str) -> dict[str, object]:
        try:
            async with self._http_client_factory() as client:
                response = await client.get(
                    GOOGLE_USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError
            return payload
        except (httpx.HTTPError, TypeError, ValueError) as exc:
            raise GoogleOAuthError(
                "Google account identity could not be verified."
            ) from exc

    def _load_pending(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> _PendingAuthorization | None:
        storage = self._storage(authority, connection_id)
        stored = storage.get_sync_with_expiry(
            GOOGLE_OAUTH_PENDING_KEY, collection=_OAUTH_COLLECTION
        )
        if stored is None:
            return None
        pending, expires_at = stored
        credential = self._google.resolve_client_credential(authority, connection_id)
        if credential is None or (
            pending.get("oauth_generation") != credential.oauth_generation
            or pending.get("credential_id") != credential.credential_id
        ):
            try:
                storage.delete_sync_if_unchanged(
                    GOOGLE_OAUTH_PENDING_KEY,
                    pending,
                    collection=_OAUTH_COLLECTION,
                    expires_at=expires_at,
                )
            except SecretGuardMismatchError:
                return None
            except Exception as exc:
                logger.warning(
                    "Stale Google OAuth pending cleanup deferred",
                    data={
                        "event": "google_oauth_stale_cleanup_deferred",
                        "connection_id": connection_id,
                        "error_type": type(exc).__name__,
                    },
                )
            return None
        return _PendingAuthorization(
            payload=pending,
            expires_at=expires_at,
            credential=credential,
        )

    def _require_connection(
        self, authority: ExecutionAuthority, connection_id: str | None
    ) -> GoogleConnection:
        connection = self._connections.get_google_connection_for_authority(
            authority, connection_id
        )
        if connection is None:
            raise GoogleOAuthError("Google connection was not found.")
        return connection

    def _storage(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> EncryptedOAuthStorage:
        return EncryptedOAuthStorage(
            secrets=self._secrets,
            authority=authority,
            namespace=f"{GOOGLE_OAUTH_NAMESPACE}.{connection_id}",
        )


def _google_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(get_default_api_timeout()),
        follow_redirects=False,
        trust_env=False,
    )


def _required_identity(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GoogleOAuthError("Google account identity response was incomplete.")
    return value


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise GoogleOAuthError("Stored Google OAuth pending state is invalid.")
    return tuple(str(item) for item in value if str(item))
