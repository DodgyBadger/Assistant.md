"""Typed contracts for principal-owned MCP connections."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MCPTransport(StrEnum):
    """Remote transports supported by the initial MCP integration."""

    STREAMABLE_HTTP = "streamable_http"
    SSE = "sse"
    ADVANCED_SHELL_STDIO = "advanced_shell_stdio"


class MCPAuthMode(StrEnum):
    """Credential modes stored independently from connection metadata."""

    NONE = "none"
    BEARER = "bearer"
    HEADER = "header"
    OAUTH = "oauth"


@dataclass(frozen=True)
class MCPStdioConfig:
    """Sanitized structured launch definition inside the advanced shell."""

    executable: str
    arguments: tuple[str, ...]
    working_directory: str
    environment: tuple[tuple[str, str], ...] = ()
    roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class MCPConnection:
    """Sanitized connection definition visible to its owning principal."""

    connection_id: str
    slug: str
    display_name: str
    url: str | None
    transport: MCPTransport
    auth_mode: MCPAuthMode
    header_name: str | None
    enabled: bool
    allow_private_http: bool
    allowed_tools: tuple[str, ...] | None
    credential_present: bool
    config_version: int
    created_at: str
    updated_at: str
    oauth_client_id: str | None = None
    oauth_client_secret_present: bool = False
    oauth_scopes: tuple[str, ...] | None = None
    stdio: MCPStdioConfig | None = None

    def require_url(self) -> str:
        """Return the HTTP endpoint or reject a transport-incompatible operation."""
        if self.url is None:
            raise ValueError("HTTP MCP connection URL is unavailable.")
        return self.url


@dataclass(frozen=True)
class MCPConnectionCreate:
    """User-controlled fields accepted when creating a connection."""

    display_name: str
    url: str | None = None
    transport: MCPTransport = MCPTransport.STREAMABLE_HTTP
    auth_mode: MCPAuthMode = MCPAuthMode.NONE
    header_name: str | None = None
    enabled: bool = True
    allow_private_http: bool = False
    allowed_tools: tuple[str, ...] | None = None
    credential: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scopes: tuple[str, ...] | None = None
    stdio: MCPStdioConfig | None = None


@dataclass(frozen=True)
class MCPConnectionUpdate:
    """Mutable connection fields; identity and ownership are intentionally absent."""

    display_name: str
    url: str | None
    transport: MCPTransport
    auth_mode: MCPAuthMode
    header_name: str | None
    enabled: bool
    allow_private_http: bool
    allowed_tools: tuple[str, ...] | None
    oauth_client_id: str | None = None
    oauth_scopes: tuple[str, ...] | None = None
    stdio: MCPStdioConfig | None = None


@dataclass(frozen=True)
class MCPConnectionTestResult:
    """Sanitized connection-test response independent of transport errors."""

    status: str
    ready: bool
    tool_count: int | None
    tool_names: tuple[str, ...]
    message: str
