"""Real MCP sessions share cold starts and close across manager lifecycle changes."""

from __future__ import annotations

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.mcp_http_inputs import mcp_http_server

pytestmark = pytest.mark.scenario


async def test_mcp_manager_shares_initialization_and_closes_each_real_session(
    app: Application,
):
    await app.start()
    from core.identity import ExecutionAuthority
    from core.mcp import MCPAuthMode, MCPConnectionCreate, MCPConnectionManager
    from core.runtime.state import get_runtime_context

    app.evidence.register_secret("owner-token", "other-token")
    service = get_runtime_context().mcp_connections
    owner, other = ExecutionAuthority("mcp-owner"), ExecutionAuthority("mcp-other")
    async with mcp_http_server(app) as server:
        first = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Local Gmail",
                url=server.url,
                allow_private_http=True,
                auth_mode=MCPAuthMode.BEARER,
                credential="owner-token",
            ),
        )
        second = service.create_connection_for_authority(
            other,
            MCPConnectionCreate(
                display_name="Local Gmail",
                url=server.url,
                allow_private_http=True,
                auth_mode=MCPAuthMode.BEARER,
                credential="other-token",
            ),
        )
        manager = MCPConnectionManager(connections=service, idle_timeout_seconds=0)
        try:
            leases = await asyncio.gather(
                manager.acquire(owner, first), manager.acquire(owner, first)
            )
            assert server.initialize_count() == 1
            other_lease = await manager.acquire(other, second)
            assert server.initialize_count() == 2
            await leases[0].close()
            assert await manager.evict_idle() == 0
            await leases[1].close()
            assert await manager.evict_idle() == 1
            replacement = await manager.acquire(owner, first)
            assert server.initialize_count() == 3
            manager.invalidate(owner.principal_id, first.connection_id)
            await replacement.close()
            renewed = await manager.acquire(owner, first)
            assert server.initialize_count() == 4
            await renewed.close()
            await other_lease.close()
        finally:
            await manager.shutdown()
        closed = server.closed_sessions()
        assert len(closed) == 4 and set(closed.values()) == {1}
        assert {
            row["authorization"]
            for row in server.requests
            if row["rpc_method"] == "initialize"
        } == {"Bearer owner-token", "Bearer other-token"}
