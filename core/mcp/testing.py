"""Bounded, sanitized MCP connection testing."""

from __future__ import annotations

import asyncio

import httpx
from fastmcp import Client
from fastmcp.client.transports import SSETransport, StreamableHttpTransport

from core.logger import UnifiedLogger
from core.web.security import sanitize_url_for_log

from .models import (
    MCPAuthMode,
    MCPConnection,
    MCPConnectionTestResult,
    MCPTransport,
)

MCP_TEST_TIMEOUT_SECONDS = 10.0
MCP_TEST_INIT_TIMEOUT_SECONDS = 8.0
MCP_TEST_READ_TIMEOUT_SECONDS = 8.0
MCP_TEST_MAX_TOOL_PAGES = 10
MCP_TEST_MAX_RETURNED_TOOL_NAMES = 100

logger = UnifiedLogger(tag="mcp-connection-test")


async def test_mcp_connection_runtime(
    connection: MCPConnection,
    credential: str | None,
) -> MCPConnectionTestResult:
    """Initialize one client and list tools without retaining runtime resources."""
    configuration_error = _validate_auth_configuration(connection, credential)
    if configuration_error is not None:
        return configuration_error

    try:
        headers, auth = _build_auth(connection, credential)
        transport = (
            StreamableHttpTransport(
                connection.require_url(),
                headers=headers,
                auth=auth,
            )
            if connection.transport is MCPTransport.STREAMABLE_HTTP
            else SSETransport(
                connection.require_url(),
                headers=headers,
                auth=auth,
            )
        )
        async with asyncio.timeout(MCP_TEST_TIMEOUT_SECONDS):
            async with Client(
                transport,
                init_timeout=MCP_TEST_INIT_TIMEOUT_SECONDS,
                timeout=MCP_TEST_READ_TIMEOUT_SECONDS,
            ) as client:
                tools = await client.list_tools(max_pages=MCP_TEST_MAX_TOOL_PAGES)
    except TimeoutError:
        return _failed_result(
            connection,
            status="timeout",
            message="Connection timed out before MCP initialization completed.",
            error_type="TimeoutError",
        )
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if status_code in {401, 403}:
            return _failed_result(
                connection,
                status="authentication_failed",
                message=(
                    f"Server rejected authentication (HTTP {status_code}). "
                    "Check the configured authentication mode and credential."
                ),
                error_type=type(exc).__name__,
            )
        return _failed_result(
            connection,
            status="http_error",
            message=f"Server returned HTTP {status_code} during MCP initialization.",
            error_type=type(exc).__name__,
        )
    except httpx.TimeoutException as exc:
        return _failed_result(
            connection,
            status="timeout",
            message="Connection timed out before MCP initialization completed.",
            error_type=type(exc).__name__,
        )
    except httpx.RequestError as exc:
        return _failed_result(
            connection,
            status="unreachable",
            message="The MCP server could not be reached from Assistant.md.",
            error_type=type(exc).__name__,
        )
    except Exception as exc:
        return _failed_result(
            connection,
            status="connection_failed",
            message="The server did not complete a valid MCP initialization.",
            error_type=type(exc).__name__,
        )

    server_tool_names = tuple(str(tool.name) for tool in tools)
    if connection.allowed_tools is None:
        effective_tool_names = server_tool_names
    else:
        allowed = set(connection.allowed_tools)
        effective_tool_names = tuple(
            name for name in server_tool_names if name in allowed
        )
    returned_names = effective_tool_names[:MCP_TEST_MAX_RETURNED_TOOL_NAMES]
    count = len(effective_tool_names)
    message = f"Connected successfully and discovered {count} available MCP tool(s)."
    if count > len(returned_names):
        message += f" Showing the first {len(returned_names)} names."
    result = MCPConnectionTestResult(
        status="ready",
        ready=True,
        tool_count=count,
        tool_names=returned_names,
        message=message,
    )
    logger.info(
        "MCP connection test succeeded",
        data={
            "event": "mcp_connection_test_succeeded",
            "connection_id": connection.connection_id,
            "url": sanitize_url_for_log(connection.require_url()),
            "transport": connection.transport.value,
            "tool_count": count,
        },
    )
    return result


def _validate_auth_configuration(
    connection: MCPConnection, credential: str | None
) -> MCPConnectionTestResult | None:
    if connection.auth_mode is MCPAuthMode.OAUTH:
        return MCPConnectionTestResult(
            status="authentication_required",
            ready=False,
            tool_count=None,
            tool_names=(),
            message="Connect OAuth for this MCP server before testing it.",
        )
    if (
        connection.auth_mode in {MCPAuthMode.BEARER, MCPAuthMode.HEADER}
        and not credential
    ):
        return MCPConnectionTestResult(
            status="credential_missing",
            ready=False,
            tool_count=None,
            tool_names=(),
            message="Set the configured static credential before testing this server.",
        )
    return None


def _build_auth(
    connection: MCPConnection, credential: str | None
) -> tuple[dict[str, str] | None, str | None]:
    if connection.auth_mode is MCPAuthMode.BEARER:
        return None, credential
    if connection.auth_mode is MCPAuthMode.HEADER:
        if connection.header_name is None or credential is None:
            raise ValueError("Header authentication configuration is incomplete.")
        return {connection.header_name: credential}, None
    return None, None


def _failed_result(
    connection: MCPConnection,
    *,
    status: str,
    message: str,
    error_type: str,
) -> MCPConnectionTestResult:
    logger.warning(
        "MCP connection test failed",
        data={
            "event": "mcp_connection_test_failed",
            "connection_id": connection.connection_id,
            "url": sanitize_url_for_log(connection.require_url()),
            "transport": connection.transport.value,
            "status": status,
            "error_type": error_type,
        },
    )
    return MCPConnectionTestResult(
        status=status,
        ready=False,
        tool_count=None,
        tool_names=(),
        message=message,
    )
