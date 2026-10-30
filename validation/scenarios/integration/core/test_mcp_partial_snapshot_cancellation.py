"""Cancellation releases a real lease acquired before another server settles."""

import asyncio

import pytest

from validation.scenarios.support.mcp_http_inputs import mcp_http_server

pytestmark = pytest.mark.scenario


async def test_cancelled_partial_snapshot_releases_already_acquired_connection(app):
    await app.start()
    from core.identity import ExecutionAuthority
    from core.mcp import MCPConnectionCreate, MCPConnectionManager
    from core.runtime.state import get_runtime_context

    service = get_runtime_context().mcp_connections
    owner = ExecutionAuthority("partial-snapshot-owner")
    async with mcp_http_server(app) as ready, mcp_http_server(app) as held:
        first = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Ready local server",
                url=ready.url,
                allow_private_http=True,
            ),
        )
        manager = MCPConnectionManager(connections=service, idle_timeout_seconds=0)
        snapshot = None
        try:
            warm = await manager.acquire(owner, first)
            await warm.close()
            service.create_connection_for_authority(
                owner,
                MCPConnectionCreate(
                    display_name="Held local server",
                    url=held.url,
                    allow_private_http=True,
                ),
            )
            held.list_release.clear()
            snapshot = asyncio.create_task(manager.acquire_snapshot(owner))
            await asyncio.wait_for(held.list_entered.wait(), timeout=10)
            snapshot.cancel()
            with pytest.raises(asyncio.CancelledError):
                await snapshot
            # The first lease was reused before the held cold start. It can be
            # evicted now only if snapshot cancellation actually released it.
            assert ready.initialize_count() == 1
            assert await manager.evict_idle() == 1
            assert list(ready.closed_sessions().values()) == [1]
        finally:
            held.list_release.set()
            await manager.shutdown()
            if snapshot is not None:
                await asyncio.gather(snapshot, return_exceptions=True)
        assert list(held.closed_sessions().values()) == [1]


async def test_endpoint_and_credential_changes_reject_stale_cold_start(app):
    await app.start()
    from dataclasses import replace

    from core.identity import ExecutionAuthority, use_execution_authority
    from core.mcp import (
        MCPAuthMode,
        MCPConnectionCreate,
        MCPConnectionManager,
        MCPConnectionUpdate,
    )
    from core.runtime.state import get_runtime_context

    service = get_runtime_context().mcp_connections
    owner = ExecutionAuthority("credential-race-owner")
    app.evidence.register_secret("race-old-token", "race-new-token")
    async with mcp_http_server(app) as old, mcp_http_server(app) as new:
        old.list_release.clear()
        connection = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Old endpoint",
                url=old.url,
                allow_private_http=True,
                auth_mode=MCPAuthMode.BEARER,
                credential="race-old-token",
            ),
        )
        manager = MCPConnectionManager(connections=service)
        task = asyncio.create_task(manager.acquire(owner, connection))
        try:
            await asyncio.wait_for(old.list_entered.wait(), timeout=10)
            with use_execution_authority(owner):
                service.update_connection(
                    connection.connection_id,
                    MCPConnectionUpdate(
                        display_name="New endpoint",
                        url=new.url,
                        transport=connection.transport,
                        auth_mode=connection.auth_mode,
                        header_name=None,
                        allowed_tools=None,
                        enabled=True,
                        allow_private_http=True,
                    ),
                )
                current = service.set_credential(
                    connection.connection_id, "race-new-token"
                )
            old.list_release.set()
            with pytest.raises(RuntimeError, match="changed while connecting"):
                await task
            assert list(old.closed_sessions().values()) == [1]
            assert new.initialize_count() == 0
            for candidate in (connection, replace(current, url=old.url)):
                with pytest.raises(RuntimeError, match="not current"):
                    await manager.acquire(owner, candidate)
            lease = await manager.acquire(owner, current)
            assert {
                r["authorization"]
                for r in old.requests
                if r["rpc_method"] == "initialize"
            } == {"Bearer race-old-token"}
            assert {
                r["authorization"]
                for r in new.requests
                if r["rpc_method"] == "initialize"
            } == {"Bearer race-new-token"}
            # Worker-thread invalidation must acknowledge stale marking while
            # keeping the active real lease usable until its holder releases it.
            await asyncio.to_thread(
                manager.invalidate, owner.principal_id, current.connection_id
            )
            assert not new.closed_sessions()
            await lease.client.call_tool("search_messages", {"query": "still-active"})
            await lease.close()
            assert list(new.closed_sessions().values()) == [1]
        finally:
            old.list_release.set()
            await manager.shutdown()
            await asyncio.gather(task, return_exceptions=True)
