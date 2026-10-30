"""Real MCP initialization, calls, roots and lifecycle over native SSH stdio."""

import asyncio

import pytest

from validation.scenarios.support.mcp_http_inputs import mcp_http_server
from validation.scenarios.support.ssh_endpoint import ssh_endpoint
from validation.scenarios.support.ssh_mcp_inputs import (
    add_ssh_mcp_connection,
    save_mcp_observations,
    ssh_mcp_manager,
)

pytestmark = pytest.mark.scenario


async def test_mcp_over_ssh_preserves_launch_roots_and_closes_the_real_session(app):
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY

    async with ssh_endpoint(app) as endpoint:
        async with ssh_mcp_manager(app, endpoint) as (service, manager):
            connection, remote = add_ssh_mcp_connection(
                endpoint, service, "First provider"
            )
            lease = await manager.acquire(LOCAL_USER_AUTHORITY, connection)
            app.evidence.step("ssh_mcp_acquired")
            assert [tool.name for tool in lease.tools] == ["echo"]
            result = await lease.client.call_tool(
                "echo", {"text": "actual SSH exchange"}
            )
            app.evidence.step("ssh_mcp_called")
            assert result.content[0].text == "echo:actual SSH exchange"
            assert remote.launches == [
                {
                    "executable": "/usr/bin/fixture-mcp",
                    "args": ["First provider", "literal ; $HOME"],
                    "cwd": "/workspace",
                    "env": {"FIXTURE_LABEL": "First provider"},
                }
            ]
            assert remote.roots == ["file:///workspace", "file:///home/advanced-shell"]
            assert remote.calls == [
                {"name": "echo", "arguments": {"text": "actual SSH exchange"}}
            ]
            assert any(
                request.get("method") == "initialize" for request in remote.requests
            )
            await lease.close()
            app.evidence.step("ssh_mcp_lease_released")
            assert await manager.evict_idle() == 1
            app.evidence.step("ssh_mcp_evicted")
            await asyncio.wait_for(remote.closed.wait(), 5)
            save_mcp_observations(app, [remote])
    assert endpoint.cleanup_completed


async def test_stdio_launch_capacity_cancellation_recovers_while_http_progresses(app):
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY
    from core.mcp import MCPConnectionCreate

    async with ssh_endpoint(app) as endpoint, mcp_http_server(app) as http:
        async with ssh_mcp_manager(app, endpoint) as (service, manager):
            first_connection, first = add_ssh_mcp_connection(
                endpoint, service, "Held provider", held=True
            )
            second_connection, second = add_ssh_mcp_connection(
                endpoint, service, "Queued provider"
            )
            first_task = asyncio.create_task(
                manager.acquire(LOCAL_USER_AUTHORITY, first_connection)
            )
            second_task = None
            try:
                await asyncio.wait_for(first.list_entered.wait(), 10)
                second_task = asyncio.create_task(
                    manager.acquire(LOCAL_USER_AUTHORITY, second_connection)
                )
                http_connection = service.create_connection_for_authority(
                    LOCAL_USER_AUTHORITY,
                    MCPConnectionCreate(
                        display_name="Independent HTTP",
                        url=http.url,
                        allow_private_http=True,
                    ),
                )
                http_lease = await asyncio.wait_for(
                    manager.acquire(LOCAL_USER_AUTHORITY, http_connection), 10
                )
                assert http.initialize_count() == 1
                assert not second.list_entered.is_set() and not second.launches
                second_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await second_task
                first_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first_task
                await asyncio.wait_for(first.closed.wait(), 5)
                recovered = await asyncio.wait_for(
                    manager.acquire(LOCAL_USER_AUTHORITY, second_connection), 10
                )
                result = await recovered.client.call_tool("echo", {"text": "recovered"})
                assert result.content[0].text == "echo:recovered"
                assert len(second.launches) == 1
                await recovered.close()
                await http_lease.close()
                assert await manager.evict_idle() == 2
                await asyncio.wait_for(second.closed.wait(), 5)
                save_mcp_observations(app, [first, second])
            finally:
                first.list_release.set()
                for task in (first_task, second_task):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(task for task in (first_task, second_task) if task is not None),
                    return_exceptions=True,
                )
    assert endpoint.cleanup_completed


async def test_stdio_session_reuse_and_shutdown_close_an_active_lease(app):
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY

    async with ssh_endpoint(app) as endpoint:
        async with ssh_mcp_manager(app, endpoint) as (service, manager):
            connection, remote = add_ssh_mcp_connection(
                endpoint, service, "Shared provider"
            )
            first = await manager.acquire(LOCAL_USER_AUTHORITY, connection)
            second = await manager.acquire(LOCAL_USER_AUTHORITY, connection)
            assert len(remote.launches) == 1
            await first.close()
            assert await manager.evict_idle() == 0
            result = await second.client.call_tool("echo", {"text": "active lease"})
            assert result.content[0].text == "echo:active lease"
            await manager.shutdown()
            await asyncio.wait_for(remote.closed.wait(), 5)
            await second.close()
            save_mcp_observations(app, [remote])
    assert endpoint.cleanup_completed


async def test_stdio_launch_setting_uses_safe_bounds_for_real_persisted_inputs(app):
    await app.start()
    from copy import deepcopy

    from core.settings import get_mcp_max_concurrent_advanced_shell_stdio_launches
    from validation.support.configuration import install_configuration

    key = "mcp_max_concurrent_advanced_shell_stdio_launches"
    original = app.configuration()
    assert get_mcp_max_concurrent_advanced_shell_stdio_launches() == 4
    observed = []
    try:
        for value, expected in ((None, 4), ("invalid", 4), (0, 1), (7, 7), (100, 32)):
            raw = deepcopy(original)
            raw["settings"][key].update(value=value, source="custom")
            # Real legacy/corruption input is installed in the SQLite store;
            # its normal cache refresh and public settings reader stay intact.
            install_configuration(app.root / "system", raw)
            actual = get_mcp_max_concurrent_advanced_shell_stdio_launches()
            assert actual == expected
            observed.append({"input": value, "effective": actual})
    finally:
        install_configuration(app.root / "system", original)
    app.evidence.save_json("stdio-launch-setting.json", observed)
