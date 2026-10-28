"""Real server workload barriers for MCP cold-start races."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from validation.pytest_scenarios.support.mcp_http_inputs import mcp_http_server


@asynccontextmanager
async def cold_start(app, *, notify=False):
    from core.identity import ExecutionAuthority
    from core.mcp import MCPConnectionCreate, MCPConnectionManager
    from core.runtime.state import get_runtime_context

    service = get_runtime_context().mcp_connections
    owner = ExecutionAuthority("mcp-race-owner")
    async with mcp_http_server(app) as server:
        server.notify_on_list = notify
        if not notify:
            server.list_release.clear()
        connection = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Race server", url=server.url, allow_private_http=True
            ),
        )
        manager = MCPConnectionManager(connections=service)
        task = asyncio.create_task(manager.acquire(owner, connection))
        try:
            await asyncio.wait_for(server.list_entered.wait(), timeout=10)
            yield service, owner, connection, manager, task, server
        finally:
            server.list_release.set()
            await manager.shutdown()
            await asyncio.gather(task, return_exceptions=True)
