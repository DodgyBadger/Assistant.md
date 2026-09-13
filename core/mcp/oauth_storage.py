"""Encrypted principal-owned key-value storage for FastMCP OAuth state."""

from __future__ import annotations

import sqlite3
from typing import Any

import httpx
from fastmcp.client.auth import OAuth
from fastmcp.client.auth.oauth import TokenStorageAdapter
from mcp.shared._httpx_utils import McpHttpClientFactory

from core.access_store import connect_access, write_transaction
from core.identity import ExecutionAuthority
from core.oauth import EncryptedOAuthStorage
from core.secrets import (
    EncryptedSecretsService,
    SecretGuardMismatchError,
    SecretIdentity,
)

from .network import MCPAsyncHTTPTransport, validate_mcp_endpoint

_OAUTH_NAMESPACE_SUFFIX = ".oauth"


class EncryptedMCPOAuthStorage(EncryptedOAuthStorage):
    """Guard OAuth reads and writes by the authoritative MCP row revision."""

    def __init__(
        self,
        *,
        secrets: EncryptedSecretsService,
        authority: ExecutionAuthority,
        connection_id: str,
        fence_token: str,
    ) -> None:
        super().__init__(
            secrets=secrets,
            authority=authority,
            namespace=f"mcp.connection.{connection_id}{_OAUTH_NAMESPACE_SUFFIX}",
        )
        self._connection_id = connection_id
        self._fence_token = fence_token

    def _check_transaction(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT oauth_fence_token FROM mcp_connections "
            "WHERE owner_principal_id=? AND connection_id=?",
            (self._authority.principal_id, self._connection_id),
        ).fetchone()
        if row is None or row["oauth_fence_token"] != self._fence_token:
            raise SecretGuardMismatchError("MCP OAuth connection state changed.")

    def _read_payload(self, target: SecretIdentity) -> str | None:
        # One read snapshot binds the row check and ciphertext. No writer lock is
        # needed until a subsequent conditional expiry cleanup or mutation.
        conn = connect_access(self._secrets._system_root)
        try:
            conn.execute("BEGIN")
            self._check_transaction(conn)
            return self._secrets.get_for_authority_on_connection(
                conn, self._authority, target.namespace, target.name
            )
        finally:
            conn.close()

    def _write_payload(self, target: SecretIdentity, payload: str) -> None:
        with write_transaction(self._secrets._system_root) as conn:
            self._check_transaction(conn)
            self._secrets.set_for_authority_on_connection(
                conn, self._authority, target.namespace, target.name, payload
            )

    def _delete_payload(self, target: SecretIdentity) -> bool:
        with write_transaction(self._secrets._system_root) as conn:
            self._check_transaction(conn)
            return self._secrets.delete_for_authority_on_connection(
                conn, self._authority, target.namespace, target.name
            )

    def _delete_payload_if_unchanged(
        self, target: SecretIdentity, expected_payload: str
    ) -> bool:
        with write_transaction(self._secrets._system_root) as conn:
            self._check_transaction(conn)
            stored = self._secrets.get_for_authority_on_connection(
                conn, self._authority, target.namespace, target.name
            )
            if stored != expected_payload:
                raise SecretGuardMismatchError(
                    "MCP OAuth authorization attempt changed."
                )
            return self._secrets.delete_for_authority_on_connection(
                conn, self._authority, target.namespace, target.name
            )

    def _check_compound_mutation(self) -> None:
        raise ValueError("MCP OAuth storage requires revision-bound operations.")


class ConnectedMCPOAuth(OAuth):
    """Use stored OAuth state without starting an interactive browser flow."""

    def __init__(
        self,
        *,
        mcp_url: str,
        token_storage: EncryptedMCPOAuthStorage,
        allow_private_http: bool,
    ) -> None:
        super().__init__(
            mcp_url=mcp_url,
            token_storage=token_storage,
            httpx_client_factory=mcp_oauth_http_client_factory(
                allow_private_http=allow_private_http
            ),
        )

    async def redirect_handler(self, authorization_url: str) -> None:
        del authorization_url
        raise ValueError(
            "MCP OAuth authorization is required. Connect this server in System."
        )

    async def callback_handler(self) -> tuple[str, str | None]:
        raise ValueError(
            "MCP OAuth authorization is required. Connect this server in System."
        )


async def has_mcp_oauth_tokens(
    *, storage: EncryptedMCPOAuthStorage, mcp_url: str
) -> bool:
    """Return whether FastMCP has persisted tokens for this server URL."""
    adapter = TokenStorageAdapter(async_key_value=storage, server_url=mcp_url)
    return await adapter.get_tokens() is not None


def _oauth_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """Create an OAuth client that cannot inherit proxies or follow redirects."""
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        auth=auth,
        follow_redirects=False,
        trust_env=False,
    )


def mcp_oauth_http_client_factory(*, allow_private_http: bool) -> McpHttpClientFactory:
    """Create clients that validate every MCP/OAuth request immediately before use."""

    async def validate_request(request: httpx.Request) -> None:
        await validate_mcp_endpoint(
            str(request.url),
            allow_private_http=allow_private_http,
        )

    def create_client(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
        **_kwargs: Any,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=False,
            trust_env=False,
            event_hooks={"request": [validate_request]},
            transport=MCPAsyncHTTPTransport(),
        )

    return create_client
