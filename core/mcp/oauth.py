"""Headless-safe interactive OAuth coordination for MCP connections."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode, urljoin, urlparse

import httpx
from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.auth.oauth import TokenStorageAdapter
from fastmcp.client.transports import SSETransport, StreamableHttpTransport
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import OAuthToken
from pydantic import AnyHttpUrl

from core.identity import ExecutionAuthority
from core.logger import UnifiedLogger
from core.oauth import (
    OAuthCompletionError,
    OAuthPKCEState,
    required_query_value,
    validate_redirect_uri,
)
from core.oauth import (
    parse_oauth_completion as parse_shared_oauth_completion,
)
from core.runtime.public_url import PublicOrigin
from core.secrets import SecretGuardMismatchError

from .manager import MCPConnectionManager
from .models import MCPAuthMode, MCPConnection, MCPTransport
from .oauth_storage import (
    EncryptedMCPOAuthStorage,
    mcp_oauth_http_client_factory,
)
from .service import MCPConnectionService, MCPMutationUnavailableError

MCP_OAUTH_START_TIMEOUT_SECONDS = 15.0
MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS = 10 * 60.0
MCP_OAUTH_COMPLETE_TIMEOUT_SECONDS = 30.0
_PENDING_COLLECTION = "assistantmd"
_PENDING_KEY = "pending-authorization"
logger = UnifiedLogger(tag="mcp-oauth")


class MCPOAuthError(ValueError):
    """Raised for sanitized, user-correctable MCP OAuth failures."""


@dataclass(frozen=True)
class MCPOAuthStart:
    auth_url: str
    state: str
    redirect_uri: str
    expires_at: str


@dataclass(frozen=True)
class MCPOAuthStatus:
    status: str
    connected: bool
    pending_expires_at: str | None = None


@dataclass(frozen=True)
class MCPOAuthRedirect:
    redirect_uri: str
    source: Literal["configured", "browser_fallback"]


def resolve_mcp_oauth_redirect(
    *,
    connection_id: str,
    public_origin: PublicOrigin | None,
    fallback_uri: str,
) -> MCPOAuthRedirect:
    """Resolve a callback from deployment config or a validated request fallback."""
    callback_path = mcp_oauth_callback_path(connection_id)
    if public_origin is not None:
        return MCPOAuthRedirect(
            redirect_uri=public_origin.build_url(callback_path),
            source="configured",
        )
    clean_fallback = _validate_redirect_uri(fallback_uri)
    if urlparse(clean_fallback).path != callback_path:
        raise MCPOAuthError("MCP OAuth redirect URI has an unexpected callback path.")
    return MCPOAuthRedirect(
        redirect_uri=clean_fallback,
        source="browser_fallback",
    )


def mcp_oauth_callback_path(connection_id: str) -> str:
    """Return the stable application callback path for one MCP connection."""
    clean_id = str(connection_id or "").strip()
    if (
        not clean_id
        or len(clean_id) > 128
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
            for character in clean_id
        )
    ):
        raise MCPOAuthError("MCP connection ID is invalid.")
    return f"/api/system/mcp/connections/{clean_id}/oauth/callback"


@dataclass
class _Attempt:
    authority: ExecutionAuthority
    connection: MCPConnection
    redirect_uri: str
    authorization_url: asyncio.Future[str]
    callback: asyncio.Future[tuple[str, str | None]]
    task: asyncio.Task[None] | None
    expires_at: datetime
    storage: EncryptedMCPOAuthStorage
    client_secret: str | None


class _HeadlessOAuth(OAuth):
    def __init__(
        self,
        *,
        mcp_url: str,
        redirect_uri: str,
        storage: EncryptedMCPOAuthStorage,
        authorization_url: asyncio.Future[str],
        callback: asyncio.Future[tuple[str, str | None]],
        allow_private_http: bool,
        scopes: tuple[str, ...] | None,
        client_id: str | None,
        client_secret: str | None,
    ) -> None:
        self._authorization_url = authorization_url
        self._callback = callback
        self._storage = storage
        self._requested_scopes = scopes
        super().__init__(
            mcp_url=mcp_url,
            token_storage=storage,
            callback_timeout=MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS,
            httpx_client_factory=mcp_oauth_http_client_factory(
                allow_private_http=allow_private_http
            ),
            scopes=list(scopes) if scopes is not None else None,
            client_id=client_id,
            client_secret=client_secret,
        )
        self.context.client_metadata.redirect_uris = [AnyHttpUrl(redirect_uri)]

    async def redirect_handler(self, authorization_url: str) -> None:
        if not self._authorization_url.done():
            self._authorization_url.set_result(authorization_url)

    async def callback_handler(self) -> tuple[str, str | None]:
        async with asyncio.timeout(MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS):
            return await self._callback

    async def _perform_authorization_code_grant(self) -> tuple[str, str]:
        """Persist enough PKCE state to finish after a process restart."""
        if self._requested_scopes is not None:
            self.context.client_metadata.scope = " ".join(self._requested_scopes)
        if self.context.client_metadata.redirect_uris is None:
            raise OAuthFlowError("No redirect URI is configured.")
        if not self.context.client_info or not self.context.client_info.client_id:
            raise OAuthFlowError("No OAuth client registration is available.")
        if (
            self.context.oauth_metadata
            and self.context.oauth_metadata.authorization_endpoint
        ):
            auth_endpoint = str(self.context.oauth_metadata.authorization_endpoint)
        else:
            auth_endpoint = urljoin(
                self.context.get_authorization_base_url(self.context.server_url),
                "/authorize",
            )
        pkce = OAuthPKCEState.generate()
        state = pkce.state
        redirect_uri = str(self.context.client_metadata.redirect_uris[0])
        auth_params = {
            "response_type": "code",
            "client_id": self.context.client_info.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": pkce.code_challenge,
            "code_challenge_method": pkce.code_challenge_method,
        }
        resource: str | None = None
        if self.context.should_include_resource_param(self.context.protocol_version):
            resource = self.context.get_resource_url()
            auth_params["resource"] = resource
        if self.context.client_metadata.scope:
            auth_params["scope"] = self.context.client_metadata.scope
        authorization_url = f"{auth_endpoint}?{urlencode(auth_params)}"
        await self._storage.put(
            _PENDING_KEY,
            {
                "state": state,
                "code_verifier": pkce.code_verifier,
                "redirect_uri": redirect_uri,
                "token_endpoint": self._get_token_endpoint(),
                "client_id": self.context.client_info.client_id,
                "client_secret": self.context.client_info.client_secret,
                "token_endpoint_auth_method": (
                    self.context.client_info.token_endpoint_auth_method or "none"
                ),
                "resource": resource,
                "expires_at": (
                    datetime.now(UTC)
                    + timedelta(seconds=MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS)
                ).isoformat(),
            },
            collection=_PENDING_COLLECTION,
            ttl=MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS,
        )
        await self.redirect_handler(authorization_url)
        auth_code, returned_state = await self.callback_handler()
        if returned_state is None or not secrets.compare_digest(returned_state, state):
            raise OAuthFlowError("OAuth state parameter mismatch.")
        if not auth_code:
            raise OAuthFlowError("No OAuth authorization code was received.")
        return auth_code, pkce.code_verifier


class MCPOAuthCoordinator:
    """Own process-local authorization attempts and encrypted durable tokens."""

    def __init__(
        self,
        *,
        connections: MCPConnectionService,
        manager: MCPConnectionManager,
    ) -> None:
        self._connections = connections
        self._manager = manager
        self._attempts: dict[tuple[str, str], _Attempt] = {}
        self._lock = asyncio.Lock()
        self._start_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._closed = False
        self._completion_tasks: set[asyncio.Task[Any]] = set()
        self._attempt_tasks: set[asyncio.Task[None]] = set()

    async def start(
        self,
        *,
        authority: ExecutionAuthority,
        connection_id: str,
        redirect_uri: str,
    ) -> MCPOAuthStart:
        key = (authority.principal_id, connection_id)
        lock = self._start_locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._start(
                authority=authority,
                connection_id=connection_id,
                redirect_uri=redirect_uri,
            )

    async def _start(
        self, *, authority: ExecutionAuthority, connection_id: str, redirect_uri: str
    ) -> MCPOAuthStart:
        if self._closed:
            raise MCPOAuthError("MCP OAuth coordinator is closed.")
        connection = self._require_oauth_connection(authority, connection_id)
        clean_redirect_uri = _validate_redirect_uri(redirect_uri)
        key = (authority.principal_id, connection.connection_id)
        await self._cancel_attempt(key)
        if self._closed:
            raise MCPOAuthError("MCP OAuth coordinator is closed.")
        # A new attempt revokes old completion and refresh writers while retaining
        # configured client credentials. Capture all material before yielding.
        connection = self._connections.disconnect_oauth(authority, connection_id)
        storage = self._connections.oauth_storage(
            authority, connection_id, expected_connection=connection
        )
        client_secret = self._connections.resolve_oauth_client_secret(
            authority, connection_id, expected_connection=connection
        )
        loop = asyncio.get_running_loop()
        attempt = _Attempt(
            authority=authority,
            connection=connection,
            redirect_uri=clean_redirect_uri,
            authorization_url=loop.create_future(),
            callback=loop.create_future(),
            task=None,
            expires_at=datetime.now(UTC)
            + timedelta(seconds=MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS),
            storage=storage,
            client_secret=client_secret,
        )
        async with self._lock:
            if self._closed:
                raise MCPOAuthError("MCP OAuth coordinator is closed.")
            self._attempts[key] = attempt
        task = asyncio.create_task(self._run_attempt(attempt))
        attempt.task = task
        self._attempt_tasks.add(task)
        task.add_done_callback(
            lambda finished: self._attempt_finished(finished, attempt)
        )
        try:
            wait_set: set[asyncio.Future[Any]] = {attempt.authorization_url, task}
            done, _ = await asyncio.wait(
                wait_set,
                timeout=MCP_OAUTH_START_TIMEOUT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if attempt.authorization_url in done:
                auth_url = attempt.authorization_url.result()
            elif task in done:
                task.result()
                raise MCPOAuthError(
                    "The MCP server completed without requesting OAuth authorization."
                )
            else:
                raise TimeoutError
        except BaseException as exc:
            await self._cancel_attempt(key, expected=attempt)
            self._clear_attempt_pending(attempt)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, MCPOAuthError):
                raise
            raise MCPOAuthError(
                "The MCP server did not start an OAuth authorization flow."
            ) from exc
        state = _required_query_value(auth_url, "state")
        return MCPOAuthStart(
            auth_url=auth_url,
            state=state,
            redirect_uri=clean_redirect_uri,
            expires_at=attempt.expires_at.isoformat(),
        )

    async def complete(
        self,
        *,
        authority: ExecutionAuthority,
        connection_id: str,
        code: str,
        state: str,
    ) -> MCPOAuthStatus:
        if self._closed:
            raise MCPOAuthError("MCP OAuth coordinator is closed.")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("MCP OAuth completion requires an async task.")
        self._completion_tasks.add(task)
        try:
            return await self._complete(
                authority=authority, connection_id=connection_id, code=code, state=state
            )
        finally:
            self._completion_tasks.discard(task)

    async def _complete(
        self,
        *,
        authority: ExecutionAuthority,
        connection_id: str,
        code: str,
        state: str,
    ) -> MCPOAuthStatus:
        key = (authority.principal_id, connection_id)
        async with self._lock:
            attempt = self._attempts.get(key)
        if attempt is None or attempt.task is None:
            connection = self._require_oauth_connection(authority, connection_id)
            await self._complete_persisted_attempt(
                authority=authority,
                connection=connection,
                code=code,
                state=state,
            )
            self._invalidate_committed(authority, connection_id, "oauth_complete")
            return MCPOAuthStatus(status="connected", connected=True)
        if datetime.now(UTC) >= attempt.expires_at:
            await self._cancel_attempt(key, expected=attempt)
            self._clear_attempt_pending(attempt)
            raise MCPOAuthError("The MCP OAuth connection attempt has expired.")
        expected_state = _required_query_value(
            await asyncio.shield(attempt.authorization_url), "state"
        )
        if not secrets.compare_digest(state, expected_state):
            raise MCPOAuthError("The MCP OAuth state did not match.")
        if attempt.callback.done():
            raise MCPOAuthError(
                "The MCP OAuth authorization attempt was already consumed."
            )
        self._consume_pending(attempt.storage, state)
        attempt.callback.set_result((code, state))
        try:
            await asyncio.wait_for(
                asyncio.shield(attempt.task),
                timeout=MCP_OAUTH_COMPLETE_TIMEOUT_SECONDS,
            )
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError):
                await self._cancel_attempt(key, expected=attempt)
                raise
            await self._cancel_attempt(key, expected=attempt)
            self._clear_attempt_pending(attempt)
            raise MCPOAuthError(
                "The MCP server did not complete OAuth authorization."
            ) from exc
        finally:
            async with self._lock:
                if self._attempts.get(key) is attempt:
                    self._attempts.pop(key, None)
        self._invalidate_committed(authority, connection_id, "oauth_complete")
        return MCPOAuthStatus(status="connected", connected=True)

    async def status(
        self, *, authority: ExecutionAuthority, connection_id: str
    ) -> MCPOAuthStatus:
        connection = self._require_oauth_connection(authority, connection_id)
        key = (authority.principal_id, connection.connection_id)
        async with self._lock:
            attempt = self._attempts.get(key)
        if attempt is not None and datetime.now(UTC) < attempt.expires_at:
            if attempt.task is not None and attempt.task.done():
                try:
                    attempt.task.result()
                except BaseException:
                    await self._cancel_attempt(key, expected=attempt)
                    return MCPOAuthStatus(status="failed", connected=False)
            return MCPOAuthStatus(
                status="pending",
                connected=False,
                pending_expires_at=attempt.expires_at.isoformat(),
            )
        if attempt is not None:
            await self._cancel_attempt(key, expected=attempt)
        adapter = TokenStorageAdapter(
            async_key_value=self._connections.oauth_storage(authority, connection_id),
            server_url=connection.require_url(),
        )
        connected = await adapter.get_tokens() is not None
        if not connected:
            pending = await self._load_pending(authority, connection)
            if pending is not None:
                return MCPOAuthStatus(
                    status="pending",
                    connected=False,
                    pending_expires_at=str(pending["expires_at"]),
                )
        return MCPOAuthStatus(
            status="connected" if connected else "disconnected",
            connected=connected,
        )

    async def disconnect(
        self, *, authority: ExecutionAuthority, connection_id: str
    ) -> None:
        connection = self._require_oauth_connection(authority, connection_id)
        key = (authority.principal_id, connection.connection_id)
        await self._cancel_attempt(key)
        self._connections.disconnect_oauth(authority, connection_id)
        self._invalidate_committed(authority, connection_id, "oauth_disconnect")

    def _invalidate_committed(
        self, authority: ExecutionAuthority, connection_id: str, mutation_kind: str
    ) -> None:
        """Preserve the saved OAuth outcome when runtime acknowledgement fails."""
        try:
            self._manager.invalidate(authority.principal_id, connection_id)
        except Exception as exc:
            logger.error(
                "MCP OAuth state committed but runtime invalidation failed",
                data={
                    "event": "connection_mutation_failed",
                    "provider": "mcp",
                    "connection_id": connection_id,
                    "mutation_kind": mutation_kind,
                    "phase": "runtime_invalidation",
                    "committed": True,
                    "error_class": type(exc).__name__,
                },
            )
            raise MCPMutationUnavailableError(
                "MCP OAuth state was saved, but runtime invalidation failed; restart Assistant.md."
            ) from exc

    async def shutdown(self) -> None:
        self._closed = True
        async with self._lock:
            keys = tuple(self._attempts)
        completion_tasks = tuple(self._completion_tasks)
        for task in completion_tasks:
            task.cancel()
        if completion_tasks:
            await asyncio.gather(*completion_tasks, return_exceptions=True)
        await asyncio.gather(*(self._cancel_attempt(key) for key in keys))
        # A superseding start can already have removed its old attempt while
        # awaiting cancellation cleanup. Retain ownership until that task settles.
        draining = tuple(self._attempt_tasks)
        for task in draining:
            if not task.cancelling():
                task.cancel()
        if draining:
            await asyncio.gather(*draining, return_exceptions=True)

    async def _run_attempt(self, attempt: _Attempt) -> None:
        auth = _HeadlessOAuth(
            mcp_url=attempt.connection.require_url(),
            redirect_uri=attempt.redirect_uri,
            storage=attempt.storage,
            authorization_url=attempt.authorization_url,
            callback=attempt.callback,
            allow_private_http=attempt.connection.allow_private_http,
            scopes=attempt.connection.oauth_scopes,
            client_id=attempt.connection.oauth_client_id,
            client_secret=attempt.client_secret,
        )
        await _prime_oauth_authorization(auth, attempt.connection.require_url())
        http_client_factory = mcp_oauth_http_client_factory(
            allow_private_http=attempt.connection.allow_private_http
        )
        transport = (
            StreamableHttpTransport(
                attempt.connection.require_url(),
                auth=auth,
                httpx_client_factory=http_client_factory,
            )
            if attempt.connection.transport is MCPTransport.STREAMABLE_HTTP
            else SSETransport(
                attempt.connection.require_url(),
                auth=auth,
                httpx_client_factory=http_client_factory,
            )
        )
        async with Client(transport, init_timeout=10.0, timeout=30.0):
            return

    def _attempt_finished(self, task: asyncio.Task[None], attempt: _Attempt) -> None:
        self._attempt_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.warning(
                "MCP OAuth authorization attempt failed",
                data={
                    "event": "mcp_oauth_attempt_failed",
                    "connection_id": attempt.connection.connection_id,
                    "error_type": type(error).__name__,
                },
            )

    async def _complete_persisted_attempt(
        self,
        *,
        authority: ExecutionAuthority,
        connection: MCPConnection,
        code: str,
        state: str,
    ) -> None:
        storage = self._connections.oauth_storage(
            authority, connection.connection_id, expected_connection=connection
        )
        pending = self._consume_pending(storage, state)
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": str(pending["redirect_uri"]),
            "client_id": str(pending["client_id"]),
            "code_verifier": str(pending["code_verifier"]),
        }
        resource = pending.get("resource")
        if isinstance(resource, str) and resource:
            token_data["resource"] = resource
        auth: httpx.Auth | None = None
        method = str(pending["token_endpoint_auth_method"])
        client_secret = pending.get("client_secret")
        if method == "client_secret_post":
            if not isinstance(client_secret, str) or not client_secret:
                raise MCPOAuthError("Stored MCP OAuth client state is incomplete.")
            token_data["client_secret"] = client_secret
        elif method == "client_secret_basic":
            if not isinstance(client_secret, str) or not client_secret:
                raise MCPOAuthError("Stored MCP OAuth client state is incomplete.")
            auth = httpx.BasicAuth(str(pending["client_id"]), client_secret)
        elif method != "none":
            raise MCPOAuthError(
                "This MCP OAuth client authentication method is not supported."
            )
        try:
            http_client_factory = mcp_oauth_http_client_factory(
                allow_private_http=connection.allow_private_http
            )
            async with http_client_factory(auth=auth) as client:
                response = await client.post(
                    str(pending["token_endpoint"]),
                    data=token_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            response.raise_for_status()
            tokens = OAuthToken.model_validate(response.json())
            adapter = TokenStorageAdapter(
                async_key_value=storage,
                server_url=connection.require_url(),
            )
            await adapter.set_tokens(tokens)
        except (httpx.HTTPError, ValueError, SecretGuardMismatchError) as exc:
            raise MCPOAuthError(
                "The MCP server rejected OAuth completion. Start a new connection attempt."
            ) from exc

    async def _load_pending(
        self, authority: ExecutionAuthority, connection: MCPConnection
    ) -> dict[str, Any] | None:
        storage = self._connections.oauth_storage(
            authority, connection.connection_id, expected_connection=connection
        )
        stored = self._pending_value(storage)
        return stored[0] if stored is not None else None

    @staticmethod
    def _pending_value(
        storage: EncryptedMCPOAuthStorage,
    ) -> tuple[dict[str, Any], float | None] | None:
        stored = storage.get_sync_with_expiry(
            _PENDING_KEY, collection=_PENDING_COLLECTION
        )
        if stored is None:
            return None
        pending, stored_expiry = stored
        required = {
            "state",
            "code_verifier",
            "redirect_uri",
            "token_endpoint",
            "client_id",
            "token_endpoint_auth_method",
            "expires_at",
        }
        try:
            if not required.issubset(pending):
                raise ValueError
            expires_at = datetime.fromisoformat(str(pending["expires_at"]))
            if expires_at.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError) as exc:
            storage.delete_sync_if_unchanged(
                _PENDING_KEY,
                pending,
                collection=_PENDING_COLLECTION,
                expires_at=stored_expiry,
            )
            raise MCPOAuthError("Stored MCP OAuth pending state is invalid.") from exc
        if datetime.now(UTC) >= expires_at:
            storage.delete_sync_if_unchanged(
                _PENDING_KEY,
                pending,
                collection=_PENDING_COLLECTION,
                expires_at=stored_expiry,
            )
            return None
        return stored

    @classmethod
    def _consume_pending(
        cls, storage: EncryptedMCPOAuthStorage, state: str
    ) -> dict[str, Any]:
        try:
            stored = cls._pending_value(storage)
            if stored is None:
                raise MCPOAuthError("No active OAuth connection attempt was found.")
            pending, expires_at = stored
            if not secrets.compare_digest(state, str(pending["state"])):
                raise MCPOAuthError("The MCP OAuth state did not match.")
            storage.delete_sync_if_unchanged(
                _PENDING_KEY,
                pending,
                collection=_PENDING_COLLECTION,
                expires_at=expires_at,
            )
            return pending
        except SecretGuardMismatchError as exc:
            raise MCPOAuthError("The MCP OAuth authorization attempt changed.") from exc

    @staticmethod
    def _clear_attempt_pending(attempt: _Attempt) -> None:
        if (
            not attempt.authorization_url.done()
            or attempt.authorization_url.cancelled()
        ):
            return
        if attempt.authorization_url.exception() is not None:
            return
        state = _required_query_value(attempt.authorization_url.result(), "state")
        try:
            stored = attempt.storage.get_sync_with_expiry(
                _PENDING_KEY, collection=_PENDING_COLLECTION
            )
            if stored is not None and stored[0].get("state") == state:
                attempt.storage.delete_sync_if_unchanged(
                    _PENDING_KEY,
                    stored[0],
                    collection=_PENDING_COLLECTION,
                    expires_at=stored[1],
                )
        except SecretGuardMismatchError:
            # The newer attempt or revoked connection owns its state now.
            return

    async def _cancel_attempt(
        self, key: tuple[str, str], *, expected: _Attempt | None = None
    ) -> None:
        async with self._lock:
            attempt = self._attempts.get(key)
            if expected is not None and attempt is not expected:
                return
            self._attempts.pop(key, None)
        if attempt is None:
            return
        if not attempt.callback.done():
            attempt.callback.cancel()
        if attempt.task is not None and not attempt.task.done():
            attempt.task.cancel()
            await asyncio.gather(attempt.task, return_exceptions=True)

    def _require_oauth_connection(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> MCPConnection:
        connection = self._connections.get_connection_for_authority(
            authority, connection_id
        )
        if connection is None:
            raise LookupError("MCP connection not found.")
        if connection.auth_mode is not MCPAuthMode.OAUTH:
            raise MCPOAuthError("This MCP connection is not configured for OAuth.")
        return connection


def parse_oauth_completion(
    *, redirect_url: str | None, code: str | None, state: str | None
) -> tuple[str, str]:
    try:
        return parse_shared_oauth_completion(
            redirect_url=redirect_url, code=code, state=state
        )
    except OAuthCompletionError as exc:
        raise MCPOAuthError(
            "MCP OAuth completion requires both code and state."
        ) from exc


def _validate_redirect_uri(value: str) -> str:
    try:
        return validate_redirect_uri(value)
    except OAuthCompletionError as exc:
        message = str(exc).replace("OAuth redirect", "MCP OAuth redirect", 1)
        raise MCPOAuthError(message) from exc


def _required_query_value(url: str, key: str) -> str:
    try:
        return required_query_value(url, key)
    except OAuthCompletionError as exc:
        raise MCPOAuthError(f"MCP OAuth authorization URL omitted {key}.") from exc


async def _prime_oauth_authorization(auth: _HeadlessOAuth, mcp_url: str) -> None:
    """Start standards discovery even when MCP initialization is public."""
    request = httpx.Request("GET", mcp_url)
    flow = auth.async_auth_flow(request)
    try:
        await anext(flow)
        response = httpx.Response(401, request=request)
        async with auth.httpx_client_factory() as client:
            while True:
                next_request = await flow.asend(response)
                if (
                    auth.context.oauth_metadata is not None
                    and auth.context.oauth_metadata.registration_endpoint is None
                    and auth.context.client_info is None
                ):
                    raise MCPOAuthError(
                        "This MCP server requires a pre-registered OAuth client ID "
                        "and client secret. Save them in the connection before "
                        "authorizing."
                    )
                if (
                    auth._authorization_url.done()
                    and next_request.url == request.url
                    and "Authorization" in next_request.headers
                ):
                    return
                response = await client.send(next_request)
    except StopAsyncIteration:
        return
    finally:
        await flow.aclose()
