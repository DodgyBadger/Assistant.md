"""Outbound network policy for user-configured MCP servers."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpcore
import httpx

_PRIVATE_MCP_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)

SocketOption = (
    tuple[int, int, int]
    | tuple[int, int, bytes | bytearray]
    | tuple[int, int, None, int]
)


class MCPNetworkPolicyError(ValueError):
    """Raised when an MCP endpoint violates outbound network policy."""


@dataclass(frozen=True)
class MCPResolvedEndpoint:
    """Validated endpoint information retained for diagnostics and tests."""

    hostname: str
    addresses: tuple[str, ...]
    secure: bool


async def validate_mcp_endpoint(
    url: str,
    *,
    allow_private_http: bool,
) -> MCPResolvedEndpoint:
    """Resolve and validate an MCP URL immediately before connecting."""
    parsed = urlsplit(str(url or "").strip())
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise MCPNetworkPolicyError("MCP servers must use HTTP or HTTPS.")
    if parsed.username or parsed.password:
        raise MCPNetworkPolicyError("MCP server URLs cannot contain credentials.")
    hostname = parsed.hostname
    if not hostname:
        raise MCPNetworkPolicyError("MCP server URL must include a hostname.")

    addresses = await resolve_mcp_addresses(hostname)
    parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
    has_private = any(_is_private_mcp_address(address) for address in parsed_addresses)
    has_public = any(address.is_global for address in parsed_addresses)
    if scheme == "http":
        if has_public or not has_private:
            raise MCPNetworkPolicyError(
                "Public HTTP MCP servers are not allowed. Use HTTPS."
            )
        if not allow_private_http:
            raise MCPNetworkPolicyError(
                "This MCP server uses HTTP on a private network. Enable private-network "
                "HTTP for this connection, or use HTTPS."
            )

    return MCPResolvedEndpoint(
        hostname=hostname,
        addresses=tuple(str(address) for address in parsed_addresses),
        secure=scheme == "https",
    )


async def resolve_mcp_addresses(hostname: str) -> tuple[str, ...]:
    """Resolve one hostname and reject the complete set unless policy allows it."""
    try:
        addresses = await asyncio.to_thread(_resolve_addresses, hostname)
    except OSError as exc:
        raise MCPNetworkPolicyError(
            "MCP server hostname could not be resolved."
        ) from exc
    if not addresses:
        raise MCPNetworkPolicyError("MCP server hostname could not be resolved.")

    parsed_addresses = tuple(ipaddress.ip_address(address) for address in addresses)
    if any(_is_forbidden_address(address) for address in parsed_addresses):
        raise MCPNetworkPolicyError("MCP server resolved to a prohibited address.")
    has_local = any(_is_private_mcp_address(address) for address in parsed_addresses)
    has_public = any(address.is_global for address in parsed_addresses)
    if has_local and has_public:
        raise MCPNetworkPolicyError(
            "MCP server hostname resolved to mixed public and local addresses."
        )
    return tuple(str(address) for address in parsed_addresses)


class MCPNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect sockets only to numeric addresses approved by MCP policy."""

    def __init__(self, *, delegate: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._delegate = delegate or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        started_at = time.monotonic()
        try:
            addresses = await asyncio.wait_for(
                resolve_mcp_addresses(host),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout(
                "MCP server hostname resolution timed out."
            ) from exc
        last_error: httpcore.ConnectError | httpcore.ConnectTimeout | None = None
        for address in addresses:
            remaining_timeout = (
                max(0.0, timeout - (time.monotonic() - started_at))
                if timeout is not None
                else None
            )
            if remaining_timeout == 0.0 and last_error is not None:
                raise last_error
            try:
                return await self._delegate.connect_tcp(
                    host=address,
                    port=port,
                    timeout=remaining_timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_error = exc
        if last_error is None:  # pragma: no cover - resolution guarantees addresses
            raise MCPNetworkPolicyError("MCP server hostname could not be resolved.")
        raise last_error

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise MCPNetworkPolicyError("MCP connections cannot use Unix sockets.")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class MCPAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX 0.28 adapter that installs the MCP policy connection pool.

    Remove this private ``_pool`` integration when a stable HTTPX release exposes
    public async network-backend injection (tracked upstream as encode/httpx#3749).
    Keep the socket-authority tests when migrating to that public API.
    """

    def __init__(self, *, network_backend: MCPNetworkBackend | None = None) -> None:
        limits = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=5.0,
        )
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=True, trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            retries=0,
            network_backend=network_backend or MCPNetworkBackend(),
        )


def _resolve_addresses(hostname: str) -> tuple[str, ...]:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        return tuple(dict.fromkeys(str(info[4][0]) for info in infos))
    return (str(literal),)


def _is_private_mcp_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return _is_private_mcp_address(address.ipv4_mapped)
    return address.is_loopback or any(
        address in network for network in _PRIVATE_MCP_NETWORKS
    )


def _is_forbidden_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
    )
