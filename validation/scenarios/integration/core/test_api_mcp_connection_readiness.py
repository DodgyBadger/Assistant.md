"""MCP connection test API uses real local protocol sessions and sanitizes errors."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.mcp_http_inputs import mcp_http_server

pytestmark = pytest.mark.scenario


async def test_mcp_test_api_reports_real_catalog_and_sanitizes_auth_failure(
    app: Application,
) -> None:
    app.evidence.register_secret(
        "mcp-api-ready-token",
        "mcp-api-denied-token",
        "credential-bearing raw transport failure",
    )
    app.vault("ApiMCPReadinessVault")
    await app.start()
    async with mcp_http_server(app) as server:
        try:
            base = "/api/system/mcp/connections"
            created = await app.request_async(
                base,
                "POST",
                {
                    "display_name": "Ready MCP",
                    "url": server.url,
                    "transport": "streamable_http",
                    "auth_mode": "bearer",
                    "enabled": True,
                    "allow_private_http": True,
                    "credential": "mcp-api-ready-token",
                },
            )
            assert created.status_code == 200
            connection = created.json()["connection_id"]
            ready = await app.request_async(f"{base}/{connection}/test", "POST")
            assert ready.status_code == 200
            payload = ready.json()
            assert payload["status"] == "ready" and payload["ready"] is True
            assert payload["tool_count"] == 2 and set(payload["tool_names"]) == {
                "search_messages",
                "archive_message",
            }
            assert "mcp-api-ready-token" not in ready.text
            assert any(
                row["rpc_method"] == "initialize"
                and row["authorization"] == "Bearer mcp-api-ready-token"
                for row in server.requests
            )
            restricted = await app.request_async(
                base,
                "POST",
                {
                    "display_name": "Restricted MCP",
                    "url": server.url,
                    "transport": "streamable_http",
                    "auth_mode": "bearer",
                    "enabled": True,
                    "allow_private_http": True,
                    "allowed_tools": ["search_messages"],
                    "credential": "mcp-api-ready-token",
                },
            )
            assert restricted.status_code == 200
            restricted_test = await app.request_async(
                f"{base}/{restricted.json()['connection_id']}/test", "POST"
            )
            assert (
                restricted_test.status_code == 200
                and restricted_test.json()["tool_names"] == ["search_messages"]
                and restricted_test.json()["tool_count"] == 1
            )
            denied = await app.request_async(
                base,
                "POST",
                {
                    "display_name": "Denied MCP",
                    "url": server.url,
                    "transport": "streamable_http",
                    "auth_mode": "bearer",
                    "enabled": True,
                    "allow_private_http": True,
                    "credential": "mcp-api-denied-token",
                },
            )
            assert denied.status_code == 200
            server.reject_status = 401
            denied_test = await app.request_async(
                f"{base}/{denied.json()['connection_id']}/test", "POST"
            )
            assert (
                denied_test.status_code == 200 and denied_test.json()["ready"] is False
            )
            assert (
                "mcp-api-denied-token" not in denied_test.text
                and "credential-bearing raw transport failure" not in denied_test.text
            )
        finally:
            server.reject_status = None
            await app.controller.stop_system()
