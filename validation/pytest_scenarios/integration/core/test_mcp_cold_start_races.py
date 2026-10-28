"""Actual MCP initialization rejects stale publication and honors shutdown."""

from __future__ import annotations

import asyncio

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.mcp_race_inputs import cold_start

pytestmark = pytest.mark.scenario


async def test_mcp_configuration_change_rejects_inflight_cold_start(app: Application):
    await app.start()
    from core.identity import use_execution_authority
    from core.mcp import MCPConnectionUpdate

    async with cold_start(app) as (service, owner, connection, manager, task, server):
        with use_execution_authority(owner):
            changed = service.update_connection(
                connection.connection_id,
                MCPConnectionUpdate(
                    display_name="Changed while initializing",
                    url=connection.url,
                    transport=connection.transport,
                    auth_mode=connection.auth_mode,
                    header_name=None,
                    enabled=True,
                    allow_private_http=True,
                    allowed_tools=None,
                ),
            )
        assert changed.config_version == connection.config_version + 1
        server.list_release.set()
        with pytest.raises(RuntimeError):
            await task
        await manager.shutdown()
        assert list(server.closed_sessions().values()) == [1]


async def test_mcp_server_catalog_notification_rejects_cold_start(app: Application):
    await app.start()
    async with cold_start(app, notify=True) as (_, _, _, manager, task, server):
        with pytest.raises(RuntimeError):
            await task
        await manager.shutdown()
        assert list(server.closed_sessions().values()) == [1]


async def test_mcp_shutdown_cancels_and_closes_inflight_cold_start(app: Application):
    await app.start()
    async with cold_start(app) as (_, _, _, manager, task, server):
        await manager.shutdown()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert list(server.closed_sessions().values()) == [1]
