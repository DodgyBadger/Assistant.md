"""Principal-owned MCP metadata and real loopback transport boundaries."""

from __future__ import annotations

import socket
from urllib.parse import urlsplit

import httpx
import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.mcp_http_inputs import mcp_http_server

pytestmark = pytest.mark.scenario


async def test_mcp_principals_keep_credentials_and_immutable_slugs(app: Application):
    await app.start()
    from core.identity import ExecutionAuthority, use_execution_authority
    from core.mcp import MCPAuthMode, MCPConnectionCreate, MCPConnectionUpdate
    from core.runtime.state import get_runtime_context

    app.evidence.register_secret("owner-token", "other-token", "owner-client-secret")
    service = get_runtime_context().mcp_connections
    owner, other = ExecutionAuthority("mcp-owner"), ExecutionAuthority("mcp-other")
    first = service.create_connection_for_authority(
        owner,
        MCPConnectionCreate(
            display_name="Gmail",
            url="https://gmail.example/mcp",
            auth_mode=MCPAuthMode.BEARER,
            credential="owner-token",
        ),
    )
    second = service.create_connection_for_authority(
        other,
        MCPConnectionCreate(
            display_name="Gmail",
            url="https://other.example/mcp",
            auth_mode=MCPAuthMode.BEARER,
            credential="other-token",
        ),
    )
    oauth = service.create_connection_for_authority(
        owner,
        MCPConnectionCreate(
            display_name="Workspace",
            url="https://workspace.example/mcp",
            auth_mode=MCPAuthMode.OAUTH,
            oauth_client_id="owner-client-id",
            oauth_client_secret="owner-client-secret",
            oauth_scopes=("mail.read", "mail.compose"),
        ),
    )
    private = service.create_connection_for_authority(
        owner,
        MCPConnectionCreate(
            display_name="Docker MCP",
            url="http://docker-mcp:8080/mcp",
            allow_private_http=True,
        ),
    )
    assert first.slug == second.slug
    assert service.get_connection_for_authority(other, first.connection_id) is None
    assert service.resolve_credential(owner, first.connection_id) == "owner-token"
    assert service.resolve_credential(other, second.connection_id) == "other-token"
    assert (
        oauth.oauth_client_id,
        oauth.oauth_client_secret_present,
        oauth.oauth_scopes,
    ) == ("owner-client-id", True, ("mail.read", "mail.compose"))
    assert private.allow_private_http and not first.allow_private_http
    assert (
        service.resolve_oauth_client_secret(owner, oauth.connection_id)
        == "owner-client-secret"
    )
    with pytest.raises(LookupError):
        service.resolve_oauth_client_secret(other, oauth.connection_id)
    with use_execution_authority(owner):
        updated = service.update_connection(
            first.connection_id,
            MCPConnectionUpdate(
                display_name="Personal Gmail",
                url=first.url,
                transport=first.transport,
                auth_mode=first.auth_mode,
                header_name=None,
                enabled=False,
                allow_private_http=False,
                allowed_tools=("search_messages",),
            ),
        )
        assert updated.slug == first.slug
        assert updated.config_version == first.config_version + 1
        assert updated.credential_present
        assert "owner-token" not in repr(updated) and "other-token" not in repr(updated)
        service.delete_connection(first.connection_id)
        replacement = service.create_connection(
            MCPConnectionCreate(
                display_name="Gmail", url="https://replacement.example/mcp"
            )
        )
    assert replacement.slug == "gmail-2"
    stored = (app.root / "system/access.db").read_bytes()
    assert all(
        secret.encode() not in stored
        for secret in ("owner-token", "other-token", "owner-client-secret")
    )
    app.evidence.save_json(
        "mcp-principal-records.json", [repr(updated), repr(oauth), repr(replacement)]
    )


async def test_mcp_connection_test_filters_tools_and_sanitizes_real_failures(
    app: Application,
):
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY
    from core.mcp import MCPAuthMode, MCPConnectionCreate
    from core.mcp.testing import test_mcp_connection_runtime
    from core.runtime.state import get_runtime_context

    app.evidence.register_secret("owner-token", "malformed-secret-detail")
    async with mcp_http_server(app) as server:
        connection = (
            get_runtime_context().mcp_connections.create_connection_for_authority(
                LOCAL_USER_AUTHORITY,
                MCPConnectionCreate(
                    display_name="Test local MCP",
                    url=server.url,
                    allow_private_http=True,
                    enabled=False,
                    auth_mode=MCPAuthMode.BEARER,
                    credential="owner-token",
                    allowed_tools=("search_messages",),
                ),
            )
        )
        ready = await test_mcp_connection_runtime(connection, "owner-token")
        assert (ready.status, ready.ready, ready.tool_count, ready.tool_names) == (
            "ready",
            True,
            1,
            ("search_messages",),
        )
        server.reject_status = 401
        rejected = await test_mcp_connection_runtime(connection, "owner-token")
        assert rejected.status == "authentication_failed"
        assert (
            "owner-token" not in rejected.message and server.url not in rejected.message
        )
        server.reject_status = None
        server.malformed_initialize = True
        malformed = await test_mcp_connection_runtime(connection, "owner-token")
        assert (malformed.status, malformed.ready) == ("connection_failed", False)
        assert "malformed-secret-detail" not in malformed.message
        server.malformed_initialize = False
        app.evidence.save_json(
            "mcp-real-test-results.json",
            [repr(ready), repr(rejected), repr(malformed)],
        )


async def test_mcp_network_policy_uses_real_pinned_loopback_sockets(app: Application):
    await app.start()
    from core.mcp.network import (
        MCPAsyncHTTPTransport,
        MCPNetworkBackend,
        MCPNetworkPolicyError,
        validate_mcp_endpoint,
    )

    async with mcp_http_server(app) as server:
        with pytest.raises(MCPNetworkPolicyError):
            await validate_mcp_endpoint(server.url, allow_private_http=False)
        allowed = await validate_mcp_endpoint(server.url, allow_private_http=True)
        assert allowed.addresses == ("127.0.0.1",)
        with pytest.raises(MCPNetworkPolicyError):
            await validate_mcp_endpoint("http://8.8.8.8/mcp", allow_private_http=True)
        assert (
            await validate_mcp_endpoint("https://8.8.8.8/mcp", allow_private_http=False)
        ).secure
        with pytest.raises(MCPNetworkPolicyError):
            await validate_mcp_endpoint(
                "https://169.254.169.254/mcp", allow_private_http=True
            )
        backend = MCPNetworkBackend()
        stream = await backend.connect_tcp(
            "127.0.0.1",
            urlsplit(server.url).port,
            timeout=5,
            local_address="127.0.0.1",
            socket_options=((socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),),
        )
        try:
            assert (
                stream.get_extra_info("socket").getsockopt(
                    socket.SOL_SOCKET, socket.SO_KEEPALIVE
                )
                == 1
            )
            await stream.write(
                b"GET /plain HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            )
            response = b""
            while chunk := await stream.read(65536, timeout=5):
                response += chunk
            assert b"200 OK" in response and response.endswith(b"ok")
        finally:
            await stream.aclose()
        async with httpx.AsyncClient(
            transport=MCPAsyncHTTPTransport(), trust_env=False
        ) as client:
            response = await client.get(server.url.replace("/mcp", "/plain"))
            assert (response.status_code, response.text) == (200, "ok")
        with pytest.raises(MCPNetworkPolicyError):
            await backend.connect_unix_socket(str(app.root / "unused.sock"))
