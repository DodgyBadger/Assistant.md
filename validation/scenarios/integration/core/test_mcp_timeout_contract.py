"""MCP timeouts retain deadline classification through real SDK cleanup."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.mcp_http_inputs import mcp_http_server

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "case", ["initialization_timeout", "total_deadline", "predeadline_http_error"]
)
async def test_mcp_connection_test_reports_actual_timeout_or_http_error(
    app: Application, case: str
) -> None:
    app.evidence.register_secret(
        "timeout-fixture-token", "credential-bearing raw transport failure"
    )
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY
    from core.mcp import MCPAuthMode, MCPConnectionCreate
    from core.mcp.testing import test_mcp_connection_runtime
    from core.runtime.state import get_runtime_context

    async with mcp_http_server(app) as server:
        service = get_runtime_context().mcp_connections
        assert service is not None
        connection = service.create_connection_for_authority(
            LOCAL_USER_AUTHORITY,
            MCPConnectionCreate(
                display_name="Timeout MCP",
                url=server.url,
                allow_private_http=True,
                enabled=False,
                auth_mode=MCPAuthMode.BEARER,
                credential="timeout-fixture-token",
            ),
        )
        if case == "initialization_timeout":
            server.initialize_delay = 9
        elif case == "total_deadline":
            server.initialize_delay = 6
            server.list_release.clear()
        else:
            server.reject_status = 500
        try:
            result = await test_mcp_connection_runtime(
                connection, "timeout-fixture-token"
            )
            assert result.ready is False
            assert result.status == (
                "http_error" if case == "predeadline_http_error" else "timeout"
            )
            assert "timeout-fixture-token" not in result.message
            assert "credential-bearing raw transport failure" not in result.message
            assert server.initialize_count() >= 1
            if case == "total_deadline":
                assert server.list_entered.is_set()
        finally:
            server.list_release.set()
            server.reject_status = None
