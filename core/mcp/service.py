"""Authorization-aware management of MCP connection definitions."""

from __future__ import annotations

import json
import re
import secrets as random_secrets
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from core.access_store import write_transaction
from core.advanced_shell.authority import require_advanced_shell_authority
from core.advanced_shell.stdio import (
    MAX_ROOTS,
    encode_structured_launch,
    validate_advanced_shell_path,
    validate_arguments,
    validate_environment,
    validate_executable,
)
from core.identity import ExecutionAuthority, require_current_execution_authority
from core.logger import UnifiedLogger
from core.secrets import (
    EncryptedSecretsService,
)

from .models import (
    MCPAuthMode,
    MCPConnection,
    MCPConnectionCreate,
    MCPConnectionUpdate,
    MCPStdioConfig,
    MCPTransport,
)
from .oauth_storage import EncryptedMCPOAuthStorage
from .schema import connect_mcp, ensure_mcp_schema

MCP_SECRET_NAMESPACE_PREFIX = "mcp.connection."
MCP_CREDENTIAL_NAME = "credential"
MCP_OAUTH_CLIENT_SECRET_NAME = "oauth_client_secret"
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")
_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
logger = UnifiedLogger(tag="mcp-connection-mutations")


class MCPMutationUnavailableError(RuntimeError):
    """Raised when committed metadata could not invalidate runtime state."""


class MCPConnectionService:
    """Manage connection metadata and credentials under execution authority."""

    def __init__(
        self,
        *,
        system_root: str,
        secrets: EncryptedSecretsService,
        on_change: Callable[[str, str], None] | None = None,
        advanced_shell_stdio_enabled: bool = False,
    ) -> None:
        self._system_root = system_root
        self._secrets = secrets
        self._on_change = on_change
        self._advanced_shell_stdio_enabled = advanced_shell_stdio_enabled
        ensure_mcp_schema(system_root)

    def list_connections(self) -> list[MCPConnection]:
        """List sanitized connections owned by the current principal."""
        return self.list_connections_for_authority(
            require_current_execution_authority()
        )

    def get_connection(self, connection_id: str) -> MCPConnection | None:
        """Return a current-principal connection without revealing other owners."""
        return self.get_connection_for_authority(
            require_current_execution_authority(), connection_id
        )

    def create_connection(self, request: MCPConnectionCreate) -> MCPConnection:
        """Create a current-principal connection and optional encrypted credential."""
        return self.create_connection_for_authority(
            require_current_execution_authority(), request
        )

    def update_connection(
        self, connection_id: str, request: MCPConnectionUpdate
    ) -> MCPConnection:
        """Update mutable metadata while preserving immutable identity and slug."""
        authority = require_current_execution_authority()
        clean_id = _required_id(connection_id)
        normalized = _normalize_update(request)
        self._require_supported_transport(normalized.transport)
        if normalized.transport is MCPTransport.ADVANCED_SHELL_STDIO:
            require_advanced_shell_authority(authority)
        delete_credential = normalized.auth_mode not in {
            MCPAuthMode.BEARER,
            MCPAuthMode.HEADER,
        }
        delete_oauth_client_secret = normalized.auth_mode is not MCPAuthMode.OAUTH
        with self._mutation(authority, clean_id, "update") as conn:
            previous = self._require_row(conn, authority, clean_id)
            delete_oauth_state = delete_oauth_client_secret or (
                previous["auth_mode"] != normalized.auth_mode.value
                or previous["url"] != normalized.url
                or previous["transport"] != normalized.transport.value
                or previous["oauth_client_id"] != normalized.oauth_client_id
                or _load_allowed_tools(previous["oauth_scopes_json"])
                != normalized.oauth_scopes
            )
            fence_token = random_secrets.token_hex(16) if delete_oauth_state else None
            cursor = conn.execute(
                """
                    UPDATE mcp_connections SET
                        display_name = ?, url = ?, transport = ?, auth_mode = ?,
                        header_name = ?, enabled = ?, allow_private_http = ?,
                        allowed_tools_json = ?,
                        oauth_client_id = ?, oauth_scopes_json = ?,
                        stdio_executable = ?, stdio_arguments_json = ?,
                        stdio_working_directory = ?, stdio_environment_json = ?,
                        stdio_roots_json = ?,
                        config_version = config_version + 1,
                        oauth_fence_token = COALESCE(?, oauth_fence_token),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE owner_principal_id = ? AND connection_id = ?
                    """,
                (
                    normalized.display_name,
                    normalized.url,
                    normalized.transport.value,
                    normalized.auth_mode.value,
                    normalized.header_name,
                    int(normalized.enabled),
                    int(normalized.allow_private_http),
                    _dump_allowed_tools(normalized.allowed_tools),
                    normalized.oauth_client_id,
                    _dump_allowed_tools(normalized.oauth_scopes),
                    normalized.stdio.executable if normalized.stdio else None,
                    _dump_stdio_arguments(normalized.stdio),
                    (normalized.stdio.working_directory if normalized.stdio else None),
                    _dump_stdio_environment(normalized.stdio),
                    _dump_stdio_roots(normalized.stdio),
                    fence_token,
                    authority.principal_id,
                    clean_id,
                ),
            )
            if cursor.rowcount == 0:
                raise LookupError("MCP connection not found.")
            namespace = _credential_namespace(clean_id)
            if delete_credential:
                self._secrets.delete_for_authority_on_connection(
                    conn, authority, namespace, MCP_CREDENTIAL_NAME
                )
            if delete_oauth_client_secret:
                self._secrets.delete_for_authority_on_connection(
                    conn, authority, namespace, MCP_OAUTH_CLIENT_SECRET_NAME
                )
            if delete_oauth_state:
                self._secrets.delete_namespace_for_authority_on_connection(
                    conn, authority, f"{namespace}.oauth"
                )
        return self._require_for_authority(authority, clean_id)

    def set_credential(self, connection_id: str, credential: str) -> MCPConnection:
        """Create or replace the current-principal encrypted static credential."""
        authority = require_current_execution_authority()
        clean_id = _required_id(connection_id)
        value = str(credential or "").strip()
        if not value:
            raise ValueError("MCP credential cannot be empty.")
        with self._mutation(authority, clean_id, "set_credential") as conn:
            connection = self._require_row(conn, authority, clean_id)
            if connection["auth_mode"] not in {MCPAuthMode.BEARER, MCPAuthMode.HEADER}:
                raise ValueError(
                    "This MCP connection does not use a static credential."
                )
            self._secrets.set_for_authority_on_connection(
                conn,
                authority,
                _credential_namespace(clean_id),
                MCP_CREDENTIAL_NAME,
                value,
            )
            conn.execute(
                "UPDATE mcp_connections SET config_version=config_version+1, updated_at=CURRENT_TIMESTAMP WHERE owner_principal_id=? AND connection_id=?",
                (authority.principal_id, clean_id),
            )
        return self._require_for_authority(authority, clean_id)

    def clear_credential(self, connection_id: str) -> MCPConnection:
        """Remove a current-principal static credential."""
        authority = require_current_execution_authority()
        clean_id = _required_id(connection_id)
        with self._mutation(authority, clean_id, "clear_credential") as conn:
            connection = self._require_row(conn, authority, clean_id)
            if connection["transport"] == MCPTransport.ADVANCED_SHELL_STDIO:
                raise ValueError(
                    "Advanced-shell stdio connections do not use credentials."
                )
            self._secrets.delete_for_authority_on_connection(
                conn, authority, _credential_namespace(clean_id), MCP_CREDENTIAL_NAME
            )
            conn.execute(
                "UPDATE mcp_connections SET config_version=config_version+1, updated_at=CURRENT_TIMESTAMP WHERE owner_principal_id=? AND connection_id=?",
                (authority.principal_id, clean_id),
            )
        return self._require_for_authority(authority, clean_id)

    def set_oauth_client_secret(
        self, connection_id: str, client_secret: str
    ) -> MCPConnection:
        """Create or replace a write-only OAuth client secret."""
        authority = require_current_execution_authority()
        clean_id = _required_id(connection_id)
        value = str(client_secret or "").strip()
        if not value:
            raise ValueError("OAuth client secret cannot be empty.")
        fence = random_secrets.token_hex(16)
        with self._mutation(authority, clean_id, "set_oauth_client_secret") as conn:
            connection = self._require_row(conn, authority, clean_id)
            if connection["auth_mode"] != MCPAuthMode.OAUTH:
                raise ValueError("This MCP connection does not use OAuth.")
            namespace = _credential_namespace(clean_id)
            self._secrets.set_for_authority_on_connection(
                conn, authority, namespace, MCP_OAUTH_CLIENT_SECRET_NAME, value
            )
            self._secrets.delete_namespace_for_authority_on_connection(
                conn, authority, f"{namespace}.oauth"
            )
            conn.execute(
                "UPDATE mcp_connections SET oauth_fence_token=?, config_version=config_version+1, updated_at=CURRENT_TIMESTAMP WHERE owner_principal_id=? AND connection_id=?",
                (fence, authority.principal_id, clean_id),
            )
        return self._require_for_authority(authority, clean_id)

    def resolve_oauth_client_secret(
        self,
        authority: ExecutionAuthority,
        connection_id: str,
        *,
        expected_connection: MCPConnection | None = None,
    ) -> str | None:
        """Resolve an OAuth client secret only after proving ownership."""
        return self._resolve_secret(
            authority, connection_id, MCP_OAUTH_CLIENT_SECRET_NAME, expected_connection
        )

    def disconnect_oauth(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> MCPConnection:
        """Fence OAuth writers and atomically clear durable OAuth state."""
        clean_id = _required_id(connection_id)
        fence = random_secrets.token_hex(16)
        with self._mutation(authority, clean_id, "disconnect_oauth") as conn:
            connection = self._require_row(conn, authority, clean_id)
            if connection["auth_mode"] != MCPAuthMode.OAUTH:
                raise ValueError("This MCP connection does not use OAuth.")
            namespace = _credential_namespace(clean_id)
            self._secrets.delete_namespace_for_authority_on_connection(
                conn, authority, f"{namespace}.oauth"
            )
            conn.execute(
                "UPDATE mcp_connections SET oauth_fence_token=?, config_version=config_version+1, updated_at=CURRENT_TIMESTAMP WHERE owner_principal_id=? AND connection_id=?",
                (fence, authority.principal_id, clean_id),
            )
        return self._require_for_authority(authority, clean_id)

    def delete_connection(self, connection_id: str) -> None:
        """Delete a current-principal connection and its encrypted credential."""
        authority = require_current_execution_authority()
        clean_id = _required_id(connection_id)
        with self._mutation(authority, clean_id, "delete") as conn:
            self._require_row(conn, authority, clean_id)
            self._secrets.delete_namespace_for_authority_on_connection(
                conn, authority, _credential_namespace(clean_id)
            )
            self._secrets.delete_namespace_for_authority_on_connection(
                conn, authority, f"{_credential_namespace(clean_id)}.oauth"
            )
            cursor = conn.execute(
                "DELETE FROM mcp_connections WHERE owner_principal_id=? AND connection_id=?",
                (authority.principal_id, clean_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("MCP connection not found.")

    def get_connection_test_material(
        self, connection_id: str
    ) -> tuple[MCPConnection, str | None]:
        """Resolve one connection and credential under current authority."""
        authority = require_current_execution_authority()
        conn = connect_mcp(self._system_root)
        try:
            conn.execute("BEGIN")
            row = self._require_row(conn, authority, connection_id)
            connection = self._connection_from_row(conn, authority, row)
            credential = self._secrets.get_for_authority_on_connection(
                conn,
                authority,
                _credential_namespace(connection_id),
                MCP_CREDENTIAL_NAME,
            )
            return connection, credential
        finally:
            conn.close()

    def list_connections_for_authority(
        self, authority: ExecutionAuthority
    ) -> list[MCPConnection]:
        """Trusted isolation helper for non-request execution and tests."""
        conn = connect_mcp(self._system_root)
        try:
            conn.execute("BEGIN")
            rows = conn.execute(
                """
                SELECT * FROM mcp_connections
                WHERE owner_principal_id = ?
                ORDER BY LOWER(display_name), connection_id
                """,
                (authority.principal_id,),
            ).fetchall()
            return [self._connection_from_row(conn, authority, row) for row in rows]
        finally:
            conn.close()

    def get_connection_for_authority(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> MCPConnection | None:
        """Trusted lookup that still scopes the query by its explicit authority."""
        conn = connect_mcp(self._system_root)
        try:
            conn.execute("BEGIN")
            row = conn.execute(
                """
                SELECT * FROM mcp_connections
                WHERE owner_principal_id = ? AND connection_id = ?
                """,
                (authority.principal_id, _required_id(connection_id)),
            ).fetchone()
            return (
                self._connection_from_row(conn, authority, row)
                if row is not None
                else None
            )
        finally:
            conn.close()

    def create_connection_for_authority(
        self, authority: ExecutionAuthority, request: MCPConnectionCreate
    ) -> MCPConnection:
        """Trusted creation helper used to prove principal isolation."""
        normalized = _normalize_create(request)
        self._require_supported_transport(normalized.transport)
        if normalized.transport is MCPTransport.ADVANCED_SHELL_STDIO:
            require_advanced_shell_authority(authority)
        connection_id = str(uuid4())
        fence_token = random_secrets.token_hex(16)
        with self._mutation(authority, connection_id, "create") as conn:
            slug = _unique_slug(conn, authority, normalized.display_name)
            conn.execute(
                """
                    INSERT INTO mcp_connection_slugs (
                        connection_id, owner_principal_id, slug
                    ) VALUES (?, ?, ?)
                    """,
                (connection_id, authority.principal_id, slug),
            )
            conn.execute(
                """
                    INSERT INTO mcp_connections (
                        connection_id, owner_principal_id, slug, display_name,
                        url, transport, auth_mode, header_name, enabled,
                        allow_private_http, allowed_tools_json
                        , oauth_client_id, oauth_scopes_json,
                        stdio_executable, stdio_arguments_json,
                        stdio_working_directory, stdio_environment_json,
                        stdio_roots_json, oauth_fence_token
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?)
                    """,
                (
                    connection_id,
                    authority.principal_id,
                    slug,
                    normalized.display_name,
                    normalized.url,
                    normalized.transport.value,
                    normalized.auth_mode.value,
                    normalized.header_name,
                    int(normalized.enabled),
                    int(normalized.allow_private_http),
                    _dump_allowed_tools(normalized.allowed_tools),
                    normalized.oauth_client_id,
                    _dump_allowed_tools(normalized.oauth_scopes),
                    normalized.stdio.executable if normalized.stdio else None,
                    _dump_stdio_arguments(normalized.stdio),
                    (normalized.stdio.working_directory if normalized.stdio else None),
                    _dump_stdio_environment(normalized.stdio),
                    _dump_stdio_roots(normalized.stdio),
                    fence_token,
                ),
            )
            namespace = _credential_namespace(connection_id)
            if normalized.credential is not None:
                self._secrets.set_for_authority_on_connection(
                    conn,
                    authority,
                    namespace,
                    MCP_CREDENTIAL_NAME,
                    normalized.credential,
                )
            if normalized.oauth_client_secret is not None:
                self._secrets.set_for_authority_on_connection(
                    conn,
                    authority,
                    namespace,
                    MCP_OAUTH_CLIENT_SECRET_NAME,
                    normalized.oauth_client_secret,
                )
        return self._require_for_authority(authority, connection_id)

    def _require_supported_transport(self, transport: MCPTransport) -> None:
        if (
            transport is MCPTransport.ADVANCED_SHELL_STDIO
            and not self._advanced_shell_stdio_enabled
        ):
            raise ValueError("Advanced-shell stdio requires advanced execution mode.")

    def resolve_credential(
        self,
        authority: ExecutionAuthority,
        connection_id: str,
        *,
        expected_connection: MCPConnection | None = None,
    ) -> str | None:
        """Resolve a credential only after proving matching connection ownership."""
        return self._resolve_secret(
            authority, connection_id, MCP_CREDENTIAL_NAME, expected_connection
        )

    def _resolve_secret(
        self,
        authority: ExecutionAuthority,
        connection_id: str,
        name: str,
        expected_connection: MCPConnection | None,
    ) -> str | None:
        conn = connect_mcp(self._system_root)
        try:
            conn.execute("BEGIN")
            self._require_expected_row(
                conn, authority, connection_id, expected_connection
            )
            return self._secrets.get_for_authority_on_connection(
                conn, authority, _credential_namespace(connection_id), name
            )
        finally:
            conn.close()

    def oauth_storage(
        self,
        authority: ExecutionAuthority,
        connection_id: str,
        *,
        expected_connection: MCPConnection | None = None,
    ) -> EncryptedMCPOAuthStorage:
        """Return encrypted OAuth storage after proving connection ownership."""
        conn = connect_mcp(self._system_root)
        try:
            row = self._require_expected_row(
                conn, authority, connection_id, expected_connection
            )
            fence_token = str(row["oauth_fence_token"])
        finally:
            conn.close()
        return EncryptedMCPOAuthStorage(
            secrets=self._secrets,
            authority=authority,
            connection_id=connection_id,
            fence_token=fence_token,
        )

    def _require_expected_row(
        self,
        conn: sqlite3.Connection,
        authority: ExecutionAuthority,
        connection_id: str,
        expected: MCPConnection | None,
    ) -> sqlite3.Row:
        row = self._require_row(conn, authority, connection_id)
        if expected is not None and (
            expected.connection_id != connection_id
            or expected.config_version != int(row["config_version"])
        ):
            raise ValueError(
                "MCP connection configuration changed before credential resolution."
            )
        return row

    def _connection_from_row(
        self, conn: sqlite3.Connection, authority: ExecutionAuthority, row: sqlite3.Row
    ) -> MCPConnection:
        values = dict(row)
        connection_id = str(values["connection_id"])
        credential_present = bool(
            self._secrets.get_for_authority_on_connection(
                conn,
                authority,
                _credential_namespace(connection_id),
                MCP_CREDENTIAL_NAME,
            )
        )
        oauth_client_secret_present = bool(
            self._secrets.get_for_authority_on_connection(
                conn,
                authority,
                _credential_namespace(connection_id),
                MCP_OAUTH_CLIENT_SECRET_NAME,
            )
        )
        return MCPConnection(
            connection_id=connection_id,
            slug=str(values["slug"]),
            display_name=str(values["display_name"]),
            url=str(values["url"]) if values["url"] else None,
            transport=MCPTransport(str(values["transport"])),
            auth_mode=MCPAuthMode(str(values["auth_mode"])),
            header_name=str(values["header_name"]) if values["header_name"] else None,
            enabled=bool(values["enabled"]),
            allow_private_http=bool(values["allow_private_http"]),
            allowed_tools=_load_allowed_tools(values["allowed_tools_json"]),
            credential_present=credential_present,
            oauth_client_id=(
                str(values["oauth_client_id"]) if values["oauth_client_id"] else None
            ),
            oauth_client_secret_present=oauth_client_secret_present,
            oauth_scopes=_load_allowed_tools(values["oauth_scopes_json"]),
            config_version=int(values["config_version"]),
            created_at=str(values["created_at"]),
            updated_at=str(values["updated_at"]),
            stdio=_load_stdio_config(values),
        )

    def _require_for_authority(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> MCPConnection:
        connection = self.get_connection_for_authority(authority, connection_id)
        if connection is None:
            raise LookupError("MCP connection not found.")
        return connection

    @staticmethod
    def _require_row(
        conn: sqlite3.Connection,
        authority: ExecutionAuthority,
        connection_id: str,
    ) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM mcp_connections WHERE owner_principal_id=? AND connection_id=?",
            (authority.principal_id, _required_id(connection_id)),
        ).fetchone()
        if row is None:
            raise LookupError("MCP connection not found.")
        return cast(sqlite3.Row, row)

    @contextmanager
    def _mutation(
        self, authority: ExecutionAuthority, connection_id: str, kind: str
    ) -> Iterator[sqlite3.Connection]:
        """Commit domain writes, then acknowledge invalidation outside the lock."""
        details = {
            "operation_id": str(uuid4()),
            "connection_id": connection_id,
            "mutation_kind": kind,
        }
        logger.info(
            "MCP connection mutation started",
            data={"event": "mcp_connection_mutation_started", **details},
        )
        try:
            with write_transaction(self._system_root) as conn:
                yield conn
        except Exception as exc:
            logger.warning(
                "MCP connection mutation rolled back",
                data={
                    "event": "connection_mutation_failed",
                    **details,
                    "provider": "mcp",
                    "phase": "transaction",
                    "committed": False,
                    "error_class": type(exc).__name__,
                },
            )
            raise
        try:
            self._notify(authority, connection_id)
        except Exception as exc:
            logger.error(
                "MCP state committed but runtime invalidation failed",
                data={
                    "event": "connection_mutation_failed",
                    **details,
                    "provider": "mcp",
                    "phase": "runtime_invalidation",
                    "committed": True,
                    "error_class": type(exc).__name__,
                },
            )
            raise MCPMutationUnavailableError(
                "MCP configuration was saved, but runtime invalidation failed; restart Assistant.md."
            ) from exc
        logger.info(
            "MCP connection mutation completed",
            data={"event": "mcp_connection_mutation_completed", **details},
        )

    def _notify(self, authority: ExecutionAuthority, connection_id: str) -> None:
        if self._on_change is not None:
            self._on_change(authority.principal_id, connection_id)


def _normalize_create(request: MCPConnectionCreate) -> MCPConnectionCreate:
    update = _normalize_update(
        MCPConnectionUpdate(
            display_name=request.display_name,
            url=request.url,
            transport=request.transport,
            auth_mode=request.auth_mode,
            header_name=request.header_name,
            enabled=request.enabled,
            allow_private_http=request.allow_private_http,
            allowed_tools=request.allowed_tools,
            oauth_client_id=request.oauth_client_id,
            oauth_scopes=request.oauth_scopes,
            stdio=request.stdio,
        )
    )
    credential = str(request.credential or "").strip() or None
    oauth_client_secret = str(request.oauth_client_secret or "").strip() or None
    if credential is not None and update.auth_mode not in {
        MCPAuthMode.BEARER,
        MCPAuthMode.HEADER,
    }:
        raise ValueError("Credentials are supported only for bearer or header auth.")
    if oauth_client_secret is not None and update.auth_mode is not MCPAuthMode.OAUTH:
        raise ValueError("OAuth client secrets are supported only for OAuth auth.")
    return MCPConnectionCreate(
        display_name=update.display_name,
        url=update.url,
        transport=update.transport,
        auth_mode=update.auth_mode,
        header_name=update.header_name,
        enabled=update.enabled,
        allow_private_http=update.allow_private_http,
        allowed_tools=update.allowed_tools,
        credential=credential,
        oauth_client_id=update.oauth_client_id,
        oauth_client_secret=oauth_client_secret,
        oauth_scopes=update.oauth_scopes,
        stdio=update.stdio,
    )


def _normalize_update(request: MCPConnectionUpdate) -> MCPConnectionUpdate:
    display_name = str(request.display_name or "").strip()
    if not display_name or len(display_name) > 120:
        raise ValueError("MCP display name must contain 1 to 120 characters.")
    transport = MCPTransport(request.transport)
    stdio = _normalize_stdio(request.stdio)
    url = (
        None
        if transport is MCPTransport.ADVANCED_SHELL_STDIO
        else _sanitize_url(request.url)
    )
    auth_mode = MCPAuthMode(request.auth_mode)
    header_name = str(request.header_name or "").strip() or None
    if auth_mode is MCPAuthMode.HEADER:
        if header_name is None or not _HEADER_NAME_PATTERN.fullmatch(header_name):
            raise ValueError("Header auth requires a valid HTTP header name.")
    elif header_name is not None:
        raise ValueError("Header name is valid only for header auth.")
    allowed_tools = _normalize_allowed_tools(request.allowed_tools)
    oauth_client_id = str(request.oauth_client_id or "").strip() or None
    oauth_scopes = _normalize_allowed_tools(request.oauth_scopes)
    if auth_mode is not MCPAuthMode.OAUTH and (oauth_client_id or oauth_scopes):
        raise ValueError("OAuth client settings are valid only for OAuth auth.")
    if transport is MCPTransport.ADVANCED_SHELL_STDIO:
        if auth_mode is not MCPAuthMode.NONE or header_name is not None:
            raise ValueError(
                "Advanced-shell stdio connections do not support authentication."
            )
        if request.allow_private_http or oauth_client_id or oauth_scopes:
            raise ValueError(
                "HTTP and OAuth settings are invalid for advanced-shell stdio."
            )
        if stdio is None:
            raise ValueError("Advanced-shell stdio launch configuration is required.")
    elif stdio is not None:
        raise ValueError(
            "Advanced-shell stdio configuration requires advanced_shell_stdio transport."
        )
    return MCPConnectionUpdate(
        display_name=display_name,
        url=url,
        transport=transport,
        auth_mode=auth_mode,
        header_name=header_name,
        enabled=bool(request.enabled),
        allow_private_http=bool(request.allow_private_http),
        allowed_tools=allowed_tools,
        oauth_client_id=oauth_client_id,
        oauth_scopes=oauth_scopes,
        stdio=stdio,
    )


def _normalize_stdio(value: MCPStdioConfig | None) -> MCPStdioConfig | None:
    if value is None:
        return None
    arguments = validate_arguments(tuple(str(item) for item in value.arguments))
    environment = validate_environment(
        tuple((str(name), str(item)) for name, item in value.environment)
    )
    roots = tuple(
        validate_advanced_shell_path(root, label="Root") for root in value.roots
    )
    if len(roots) > MAX_ROOTS:
        raise ValueError("Advanced-shell stdio has too many Roots.")
    normalized = MCPStdioConfig(
        executable=validate_executable(value.executable),
        arguments=arguments,
        working_directory=validate_advanced_shell_path(
            value.working_directory, label="working directory"
        ),
        environment=environment,
        roots=roots,
    )
    encode_structured_launch(
        executable=normalized.executable,
        arguments=normalized.arguments,
        working_directory=normalized.working_directory,
        environment=normalized.environment,
    )
    return normalized


def _sanitize_url(value: str | None) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("MCP URL must be an absolute HTTP or HTTPS URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("MCP URL cannot contain credentials.")
    if parsed.query:
        raise ValueError(
            "MCP URL cannot contain query parameters; configure credentials separately."
        )
    if parsed.fragment:
        raise ValueError("MCP URL cannot contain a fragment.")
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path or "/", "", "")
    )


def _normalize_allowed_tools(values: tuple[str, ...] | None) -> tuple[str, ...] | None:
    if values is None:
        return None
    normalized = tuple(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )
    if not normalized:
        return None
    if len(normalized) > 200 or any(len(value) > 128 for value in normalized):
        raise ValueError("MCP allowed-tools policy exceeds supported limits.")
    return normalized


def _unique_slug(
    conn: sqlite3.Connection, authority: ExecutionAuthority, display_name: str
) -> str:
    base = _SLUG_PATTERN.sub("-", display_name.lower()).strip("-")[:48] or "mcp"
    candidate = base
    suffix = 2
    while conn.execute(
        "SELECT 1 FROM mcp_connection_slugs WHERE owner_principal_id = ? AND slug = ?",
        (authority.principal_id, candidate),
    ).fetchone():
        suffix_text = f"-{suffix}"
        candidate = f"{base[: 48 - len(suffix_text)]}{suffix_text}"
        suffix += 1
    return candidate


def _credential_namespace(connection_id: str) -> str:
    return f"{MCP_SECRET_NAMESPACE_PREFIX}{_required_id(connection_id)}"


def _required_id(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("MCP connection ID is required.")
    return normalized


def _dump_allowed_tools(values: tuple[str, ...] | None) -> str | None:
    return json.dumps(list(values), separators=(",", ":")) if values else None


def _load_allowed_tools(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None
    payload = json.loads(str(value))
    if not isinstance(payload, list) or not all(
        isinstance(item, str) for item in payload
    ):
        raise ValueError("Stored MCP allowed-tools policy is invalid.")
    return tuple(payload) or None


def _dump_stdio_arguments(value: MCPStdioConfig | None) -> str | None:
    return json.dumps(list(value.arguments), separators=(",", ":")) if value else None


def _dump_stdio_environment(value: MCPStdioConfig | None) -> str | None:
    return json.dumps(dict(value.environment), separators=(",", ":")) if value else None


def _dump_stdio_roots(value: MCPStdioConfig | None) -> str | None:
    return json.dumps(list(value.roots), separators=(",", ":")) if value else None


def _load_json_strings(value: object, *, label: str) -> tuple[str, ...]:
    payload = json.loads(str(value))
    if not isinstance(payload, list) or not all(
        isinstance(item, str) for item in payload
    ):
        raise ValueError(f"Stored advanced-shell stdio {label} is invalid.")
    return tuple(payload)


def _load_stdio_config(values: dict[str, object]) -> MCPStdioConfig | None:
    executable = values.get("stdio_executable")
    if executable is None:
        return None
    environment_payload = json.loads(str(values["stdio_environment_json"]))
    if not isinstance(environment_payload, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in environment_payload.items()
    ):
        raise ValueError("Stored advanced-shell stdio environment is invalid.")
    return _normalize_stdio(
        MCPStdioConfig(
            executable=str(executable),
            arguments=_load_json_strings(
                values["stdio_arguments_json"], label="arguments"
            ),
            working_directory=str(values["stdio_working_directory"]),
            environment=tuple(environment_payload.items()),
            roots=_load_json_strings(values["stdio_roots_json"], label="Roots"),
        )
    )
