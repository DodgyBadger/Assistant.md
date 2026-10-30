"""Credential/destination capture remains atomic across real asynchronous DNS."""

import asyncio

import pytest

from validation.scenarios.support.mcp_http_inputs import mcp_http_server
from validation.scenarios.support.mcp_network_environment import DNSAnswers

pytestmark = pytest.mark.scenario


async def test_endpoint_edit_during_dns_never_sends_new_credential_to_old_endpoint(
    app, monkeypatch
):
    await app.start()
    from core.identity import ExecutionAuthority, use_execution_authority
    from core.mcp import (
        MCPAuthMode,
        MCPConnectionCreate,
        MCPConnectionManager,
        MCPConnectionUpdate,
    )
    from core.runtime.state import get_runtime_context

    service = get_runtime_context().mcp_connections
    owner = ExecutionAuthority("dns-credential-owner")
    app.evidence.register_secret("dns-old-token", "dns-new-token")
    async with mcp_http_server(app) as old, mcp_http_server(app) as new:
        connection = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="DNS race",
                url=old.url.replace("127.0.0.1", "credential-race.test"),
                allow_private_http=True,
                auth_mode=MCPAuthMode.BEARER,
                credential="dns-old-token",
            ),
        )
        manager = MCPConnectionManager(connections=service)
        with monkeypatch.context() as environment:
            # Only OS DNS is controlled. Manager, endpoint policy, encrypted
            # credential resolution and HTTP sessions execute unchanged.
            dns = DNSAnswers(environment)
            dns.answers["credential-race.test"] = ("127.0.0.1",)
            dns.hold = True
            task = asyncio.create_task(manager.acquire(owner, connection))
            try:
                assert await asyncio.to_thread(dns.entered.wait, 3)
                assert not old.requests and not new.requests
                with use_execution_authority(owner):
                    service.update_connection(
                        connection.connection_id,
                        MCPConnectionUpdate(
                            display_name="New destination",
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
                        connection.connection_id, "dns-new-token"
                    )
                dns.hold = False
                dns.release.set()
                with pytest.raises(RuntimeError, match="changed while connecting"):
                    await asyncio.wait_for(task, 10)
                assert old.initialize_count() == 1 and new.initialize_count() == 0
                assert list(old.closed_sessions().values()) == [1]
                assert {row["authorization"] for row in old.requests} == {
                    "Bearer dns-old-token"
                }
                lease = await manager.acquire(owner, current)
                assert new.initialize_count() == 1
                assert {row["authorization"] for row in new.requests} == {
                    "Bearer dns-new-token"
                }
                await lease.close()
                app.evidence.save_json(
                    "credential-dns-race.json",
                    {
                        "dns_queries": dns.requests,
                        "old": old.requests,
                        "new": new.requests,
                    },
                )
            finally:
                dns.hold = False
                dns.release.set()
                await manager.shutdown()
                await asyncio.gather(task, return_exceptions=True)
