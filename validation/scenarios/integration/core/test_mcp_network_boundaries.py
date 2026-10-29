"""Actual guarded TLS plus deterministic external DNS and socket failures."""

import asyncio
import time
from urllib.parse import urlsplit

import httpcore
import httpx
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from validation.scenarios.application import Application
from validation.scenarios.support.mcp_http_inputs import mcp_http_server
from validation.scenarios.support.mcp_network_environment import (
    DNSAnswers,
    NetworkFailures,
)
from validation.scenarios.support.tls_inputs import certificate_rejection, local_tls

pytestmark = pytest.mark.scenario


async def test_guarded_mcp_https_retains_host_sni_verified_identity_and_closure(
    app: Application,
):
    await app.start()
    from core.mcp.oauth_storage import mcp_oauth_http_client_factory

    tls = local_tls(app.root / "tls")
    async with mcp_http_server(app, tls=tls) as server:
        factory = mcp_oauth_http_client_factory(
            allow_private_http=False, ssl_context=tls.client_context
        )
        transport = StreamableHttpTransport(server.url, httpx_client_factory=factory)
        async with Client(transport) as client:
            tools = await client.list_tools()
            assert {t.name for t in tools} == {"search_messages", "archive_message"}
            result = await client.call_tool("search_messages", {"query": "receipt"})
            assert "receipt" in str(result)
        assert server.closed_sessions()
        assert "localhost" in tls.names
        assert all(r["host"] == urlsplit(server.url).netloc for r in server.requests)
        async with factory() as http:
            with pytest.raises(httpx.ConnectError) as rejected:
                await http.get(server.url.replace("localhost", "127.0.0.1"))
            assert certificate_rejection(rejected.value)
        # The normal production trust store must reject the fixture's untrusted
        # certificate. No certificate verification or URL policy is disabled.
        async with mcp_oauth_http_client_factory(allow_private_http=False)() as http:
            with pytest.raises(httpx.ConnectError) as rejected:
                await http.get(server.url)
            assert certificate_rejection(rejected.value)
        app.evidence.save_json(
            "tls-boundaries.json", {"sni": tls.names, "requests": server.requests}
        )


async def test_mcp_rebinding_and_mixed_dns_fail_before_socket_dispatch(
    app: Application, monkeypatch
):
    await app.start()
    from core.mcp.network import (
        MCPNetworkBackend,
        MCPNetworkPolicyError,
        validate_mcp_endpoint,
    )

    with monkeypatch.context() as scope:
        dns = DNSAnswers(scope)
        dns.answers["mcp.example"] = ("93.184.215.14",)
        await validate_mcp_endpoint("https://mcp.example/mcp", allow_private_http=False)
        external = NetworkFailures()
        backend = MCPNetworkBackend(delegate=external)
        for changed in [("169.254.169.254",), ("93.184.215.14", "127.0.0.1")]:
            dns.answers["mcp.example"] = changed
            with pytest.raises(MCPNetworkPolicyError):
                await backend.connect_tcp("mcp.example", 443, timeout=1)
        assert external.calls == []
        app.evidence.save_json(
            "dns-rebinding.json",
            {"queries": dns.requests, "dispatches": external.calls},
        )


async def test_mcp_numeric_retry_uses_one_total_budget_and_preserves_cancellation(
    app: Application, monkeypatch
):
    await app.start()
    from core.mcp.network import MCPNetworkBackend

    with monkeypatch.context() as scope:
        dns = DNSAnswers(scope)
        dns.answers["mcp.example"] = ("93.184.215.14", "93.184.215.15", "93.184.215.16")
        external = NetworkFailures(delay=0.08)
        backend = MCPNetworkBackend(delegate=external)
        started = time.monotonic()
        with pytest.raises(httpcore.ConnectTimeout):
            await backend.connect_tcp("mcp.example", 443, timeout=0.12)
        assert time.monotonic() - started < 1
        assert [c["host"] for c in external.calls] == ["93.184.215.14", "93.184.215.15"]
        assert 0 < external.calls[1]["timeout"] < external.calls[0]["timeout"] <= 0.12
        assert dns.requests == ["mcp.example"]
        external.hold = True
        external.entered.clear()
        task = asyncio.create_task(backend.connect_tcp("mcp.example", 443, timeout=5))
        await asyncio.wait_for(external.entered.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        app.evidence.save_json("numeric-connects.json", external.calls)


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
async def test_mcp_dns_wait_is_bounded_and_never_dispatches_after_cancellation(
    app: Application, monkeypatch, mode
):
    await app.start()
    from core.mcp.network import MCPNetworkBackend

    with monkeypatch.context() as scope:
        dns = DNSAnswers(scope)
        dns.answers["mcp.example"] = ("93.184.215.14",)
        dns.hold = True
        external = NetworkFailures()
        backend = MCPNetworkBackend(delegate=external)
        task = asyncio.create_task(
            backend.connect_tcp(
                "mcp.example", 443, timeout=0.2 if mode == "timeout" else 5
            )
        )
        try:
            assert await asyncio.wait_for(asyncio.to_thread(dns.entered.wait, 2), 3)
            if mode == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(httpcore.ConnectTimeout):
                    await asyncio.wait_for(task, 2)
        finally:
            dns.release.set()
            assert await asyncio.wait_for(asyncio.to_thread(dns.exited.wait, 2), 3)
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert external.calls == []
        app.evidence.save_json(
            "dns-wait.json",
            {
                "mode": mode,
                "dns_thread_released": dns.exited.is_set(),
                "dispatches": external.calls,
            },
        )
