"""Security contracts for principal-owned MCP connection management."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import httpcore
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-mcp-domain-")
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from core.identity import ExecutionAuthority, use_execution_authority  # noqa: E402
from core.mcp import (  # noqa: E402
    MCPAuthMode,
    MCPConnection,
    MCPConnectionCreate,
    MCPConnectionManager,
    MCPConnectionService,
    MCPConnectionUpdate,
    MCPTransport,
)
from core.mcp import manager as mcp_manager  # noqa: E402
from core.mcp.network import (  # noqa: E402
    MCPAsyncHTTPTransport,
    MCPNetworkBackend,
    MCPNetworkPolicyError,
    SocketOption,
    validate_mcp_endpoint,
)
from core.mcp.testing import test_mcp_connection_runtime  # noqa: E402
from core.secrets import EncryptedSecretsService, SecretKeyring  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class MCPConnectionIsolationScenario(BaseScenario):
    """Prove connection identity, credentials, and enumeration are owner-scoped."""

    async def test_scenario(self) -> None:
        owner = ExecutionAuthority("mcp-owner")
        other = ExecutionAuthority("mcp-other")
        system_root = self.run_path / "system"
        system_root.mkdir()
        secrets = EncryptedSecretsService(
            system_root=str(system_root),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        )
        service = MCPConnectionService(
            system_root=str(system_root),
            secrets=secrets,
        )

        owner_connection = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Gmail",
                url="https://gmail.example/mcp",
                auth_mode=MCPAuthMode.BEARER,
                credential="owner-token",
            ),
        )
        other_connection = service.create_connection_for_authority(
            other,
            MCPConnectionCreate(
                display_name="Gmail",
                url="https://other.example/mcp",
                auth_mode=MCPAuthMode.BEARER,
                credential="other-token",
            ),
        )
        oauth_connection = service.create_connection_for_authority(
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
        private_http_connection = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Docker MCP",
                url="http://docker-mcp:8080/mcp",
                allow_private_http=True,
            ),
        )

        self.soft_assert_equal(
            owner_connection.slug,
            other_connection.slug,
            "Immutable model-facing slugs may repeat safely across principals",
        )
        self.soft_assert_equal(
            service.get_connection_for_authority(other, owner_connection.connection_id),
            None,
            "A foreign connection ID must look absent",
        )
        self.soft_assert_equal(
            service.resolve_credential(owner, owner_connection.connection_id),
            "owner-token",
            "Credential lookup should resolve the matching owner's value",
        )
        self.soft_assert_equal(
            service.resolve_credential(other, other_connection.connection_id),
            "other-token",
            "Same-named credentials must remain independently owned",
        )
        self.soft_assert_equal(
            (
                oauth_connection.oauth_client_id,
                oauth_connection.oauth_client_secret_present,
                oauth_connection.oauth_scopes,
            ),
            ("owner-client-id", True, ("mail.read", "mail.compose")),
            "OAuth client metadata should round-trip without exposing its secret",
        )
        self.soft_assert(
            private_http_connection.allow_private_http
            and not owner_connection.allow_private_http,
            "Private HTTP acknowledgement should persist only on the opted-in connection",
        )
        await self._assert_private_http_connection(
            service, owner, private_http_connection
        )
        self.soft_assert_equal(
            service.resolve_oauth_client_secret(owner, oauth_connection.connection_id),
            "owner-client-secret",
            "OAuth client secrets should resolve only under matching authority",
        )
        try:
            service.resolve_oauth_client_secret(other, oauth_connection.connection_id)
        except LookupError:
            pass
        else:
            self.soft_assert(
                False, "A foreign principal must not resolve OAuth secrets"
            )
        self.soft_assert(
            b"owner-client-secret" not in (system_root / "access.db").read_bytes(),
            "OAuth client secrets must be encrypted at rest",
        )

        with use_execution_authority(owner):
            updated = service.update_connection(
                owner_connection.connection_id,
                MCPConnectionUpdate(
                    display_name="Personal Gmail",
                    url=owner_connection.url,
                    transport=MCPTransport.STREAMABLE_HTTP,
                    auth_mode=MCPAuthMode.BEARER,
                    header_name=None,
                    enabled=False,
                    allow_private_http=False,
                    allowed_tools=("search_messages",),
                ),
            )
        self.soft_assert_equal(
            updated.slug,
            owner_connection.slug,
            "Display-name updates must not change the persisted tool prefix",
        )
        self.soft_assert_equal(
            updated.config_version,
            owner_connection.config_version + 1,
            "Mutable configuration should advance its invalidation version",
        )
        self.soft_assert(
            updated.credential_present
            and "owner-token" not in repr(updated)
            and "other-token" not in repr(updated),
            "Sanitized records should expose credential presence but never values",
        )
        self.soft_assert(
            b"owner-token" not in (system_root / "access.db").read_bytes(),
            "Neither MCP metadata nor encrypted storage may contain credential plaintext",
        )

        with patch("core.mcp.testing.Client", return_value=_SuccessfulTestClient()):
            ready_result = await test_mcp_connection_runtime(
                updated,
                "owner-token",
            )
        self.soft_assert_equal(
            (
                ready_result.status,
                ready_result.ready,
                ready_result.tool_count,
                ready_result.tool_names,
            ),
            ("ready", True, 1, ("search_messages",)),
            "Connection testing should report effective allowlisted tools",
        )

        with patch("core.mcp.testing.Client", return_value=_RejectedTestClient()):
            rejected_result = await test_mcp_connection_runtime(
                updated,
                "owner-token",
            )
        self.soft_assert_equal(
            rejected_result.status,
            "authentication_failed",
            "HTTP authentication rejection should have a stable sanitized status",
        )
        self.soft_assert(
            "owner-token" not in rejected_result.message
            and updated.url not in rejected_result.message,
            "Connection-test failures must not expose credentials or raw URLs",
        )

        with patch("core.mcp.testing.Client", return_value=_MalformedTestClient()):
            malformed_result = await test_mcp_connection_runtime(
                updated,
                "owner-token",
            )
        self.soft_assert_equal(
            (malformed_result.status, malformed_result.ready),
            ("connection_failed", False),
            "Malformed MCP initialization should have a stable failure status",
        )
        self.soft_assert(
            "malformed-secret-detail" not in malformed_result.message,
            "Malformed server details must not enter user-visible failures",
        )

        with patch("core.mcp.testing.Client", return_value=_TimeoutTestClient()):
            timeout_result = await test_mcp_connection_runtime(
                updated,
                "owner-token",
            )
        self.soft_assert_equal(
            timeout_result.status,
            "timeout",
            "MCP initialization timeouts should remain distinguishable",
        )

        with use_execution_authority(owner):
            service.delete_connection(owner_connection.connection_id)
            replacement = service.create_connection(
                MCPConnectionCreate(
                    display_name="Gmail",
                    url="https://replacement.example/mcp",
                )
            )
        self.soft_assert_equal(
            replacement.slug,
            "gmail-2",
            "A retired slug must never be reassigned to another connection",
        )

        await self._assert_managed_connection_lifecycle(
            service=service,
            owner=owner,
            other=other,
            owner_connection=replacement,
            other_connection=other_connection,
        )
        await self._assert_network_policy()

        self.assert_no_failures()
        self.teardown_scenario()

    async def _assert_managed_connection_lifecycle(
        self,
        *,
        service: MCPConnectionService,
        owner: ExecutionAuthority,
        other: ExecutionAuthority,
        owner_connection: MCPConnection,
        other_connection: MCPConnection,
    ) -> None:
        clients: list[_ManagedTestClient] = []

        def client_factory(*_args: object, **_kwargs: object) -> _ManagedTestClient:
            client = _ManagedTestClient()
            clients.append(client)
            return client

        manager = MCPConnectionManager(
            connections=service,
            idle_timeout_seconds=0,
        )
        with (
            patch("core.mcp.manager.Client", side_effect=client_factory),
            patch("core.mcp.manager.validate_mcp_endpoint", new=_allow_endpoint),
        ):
            owner_leases = await asyncio.gather(
                manager.acquire(owner, owner_connection),
                manager.acquire(owner, owner_connection),
            )
            self.soft_assert_equal(
                len(clients),
                1,
                "Concurrent cold callers should share one initialization",
            )
            other_lease = await manager.acquire(other, other_connection)
            self.soft_assert_equal(
                len(clients),
                2,
                "The same manager must retain separate clients per principal",
            )

            await owner_leases[0].close()
            self.soft_assert_equal(
                await manager.evict_idle(),
                0,
                "An active lease must prevent idle eviction",
            )
            await owner_leases[1].close()
            self.soft_assert_equal(
                await manager.evict_idle(),
                1,
                "An idle unleased client should be evicted",
            )
            replacement_lease = await manager.acquire(owner, owner_connection)
            self.soft_assert_equal(
                len(clients),
                3,
                "An evicted connection should initialize again on demand",
            )
            manager.invalidate(owner.principal_id, owner_connection.connection_id)
            await asyncio.sleep(0)
            await replacement_lease.close()
            await asyncio.sleep(0)
            invalidated_lease = await manager.acquire(owner, owner_connection)
            self.soft_assert_equal(
                len(clients),
                4,
                "Invalidation should force the next lease onto a fresh client",
            )
            await invalidated_lease.close()
            await other_lease.close()
            await manager.shutdown()

        self.soft_assert(
            all(client.exit_count == 1 for client in clients),
            "Eviction, invalidation, and shutdown should close every client once "
            f"(close counts: {[client.exit_count for client in clients]})",
        )

        connect_started = asyncio.Event()
        finish_connect = asyncio.Event()
        racing_client = _BlockingManagedTestClient(connect_started, finish_connect)
        racing_manager = MCPConnectionManager(connections=service)
        with (
            patch("core.mcp.manager.Client", return_value=racing_client),
            patch("core.mcp.manager.validate_mcp_endpoint", new=_allow_endpoint),
        ):
            current = service.get_connection_for_authority(
                owner, owner_connection.connection_id
            )
            assert current is not None
            stale_acquire = asyncio.create_task(racing_manager.acquire(owner, current))
            await connect_started.wait()
            with use_execution_authority(owner):
                service.update_connection(
                    owner_connection.connection_id,
                    MCPConnectionUpdate(
                        display_name=f"{current.display_name} updated",
                        url=current.url,
                        transport=current.transport,
                        auth_mode=current.auth_mode,
                        header_name=current.header_name,
                        enabled=current.enabled,
                        allow_private_http=current.allow_private_http,
                        allowed_tools=current.allowed_tools,
                        oauth_client_id=current.oauth_client_id,
                        oauth_scopes=current.oauth_scopes,
                    ),
                )
            finish_connect.set()
            try:
                await stale_acquire
            except RuntimeError:
                stale_rejected = True
            else:
                stale_rejected = False
            self.soft_assert(
                stale_rejected and racing_client.exit_count == 1,
                "A config mutation during cold start must reject and close the stale client",
            )
        await racing_manager.shutdown()

        invalidating_manager = MCPConnectionManager(connections=service)
        invalidating_client = _InvalidatingManagedTestClient(
            lambda: invalidating_manager.invalidate(
                owner.principal_id, owner_connection.connection_id
            )
        )
        with (
            patch("core.mcp.manager.Client", return_value=invalidating_client),
            patch("core.mcp.manager.validate_mcp_endpoint", new=_allow_endpoint),
        ):
            current = service.get_connection_for_authority(
                owner, owner_connection.connection_id
            )
            assert current is not None
            try:
                await invalidating_manager.acquire(owner, current)
            except RuntimeError:
                catalog_invalidation_rejected = True
            else:
                catalog_invalidation_rejected = False
            self.soft_assert(
                catalog_invalidation_rejected and invalidating_client.exit_count == 1,
                "A same-loop tool-list invalidation must precede cold-start publication",
            )
        await invalidating_manager.shutdown()

        shutdown_started = asyncio.Event()
        never_finish = asyncio.Event()
        shutdown_client = _BlockingManagedTestClient(shutdown_started, never_finish)
        shutdown_manager = MCPConnectionManager(connections=service)
        with (
            patch("core.mcp.manager.Client", return_value=shutdown_client),
            patch("core.mcp.manager.validate_mcp_endpoint", new=_allow_endpoint),
        ):
            current = service.get_connection_for_authority(
                owner, owner_connection.connection_id
            )
            assert current is not None
            shutdown_acquire = asyncio.create_task(
                shutdown_manager.acquire(owner, current)
            )
            await shutdown_started.wait()
            await shutdown_manager.shutdown()
            shutdown_result = await asyncio.gather(
                shutdown_acquire, return_exceptions=True
            )
            self.soft_assert(
                isinstance(shutdown_result[0], asyncio.CancelledError)
                and shutdown_client.exit_count == 1,
                "Shutdown must cancel and close an in-flight cold start before returning",
            )

    async def _assert_network_policy(self) -> None:
        with patch(
            "core.mcp.network._resolve_addresses",
            return_value=("172.18.0.9",),
        ):
            try:
                await validate_mcp_endpoint(
                    "http://marimo:8080/mcp/server",
                    allow_private_http=False,
                )
                rejected_local_http = False
            except MCPNetworkPolicyError:
                rejected_local_http = True
            self.soft_assert(
                rejected_local_http,
                "Private HTTP MCP should require per-connection acknowledgement",
            )
            allowed = await validate_mcp_endpoint(
                "http://marimo:8080/mcp/server",
                allow_private_http=True,
            )
            self.soft_assert_equal(
                allowed.addresses,
                ("172.18.0.9",),
                "Per-connection acknowledgement should admit private HTTP MCP",
            )
        with patch(
            "core.mcp.network._resolve_addresses",
            return_value=("8.8.8.8",),
        ):
            try:
                await validate_mcp_endpoint(
                    "http://public.example/mcp",
                    allow_private_http=True,
                )
                rejected_public_http = False
            except MCPNetworkPolicyError:
                rejected_public_http = True
            self.soft_assert(
                rejected_public_http,
                "Private HTTP acknowledgement must not permit public HTTP MCP",
            )
            secure = await validate_mcp_endpoint(
                "https://public.example/mcp",
                allow_private_http=False,
            )
            self.soft_assert(
                secure.secure,
                "Public MCP connections should require and accept HTTPS",
            )
        request_client = mcp_manager._mcp_http_client_factory(
            allow_private_http=False
        )()
        request_hook = request_client.event_hooks["request"][0]
        try:
            with patch(
                "core.mcp.network._resolve_addresses",
                side_effect=(("8.8.8.8",), ("169.254.169.254",)),
            ):
                await request_hook(httpx.Request("POST", "https://public.example/mcp"))
                try:
                    await request_hook(
                        httpx.Request("POST", "https://public.example/mcp")
                    )
                except MCPNetworkPolicyError:
                    rebound_request_rejected = True
                else:
                    rebound_request_rejected = False
        finally:
            await request_client.aclose()
        self.soft_assert(
            rebound_request_rejected,
            "Every retained-client request should recheck DNS network policy",
        )

        delegate = _RecordingNetworkBackend(failures=1)
        backend = MCPNetworkBackend(delegate=delegate)
        with patch(
            "core.mcp.network._resolve_addresses",
            return_value=("8.8.8.8", "1.1.1.1"),
        ):
            stream = await backend.connect_tcp(
                "public.example",
                443,
                timeout=5.0,
                local_address="0.0.0.0",
                socket_options=((1, 2, 3),),
            )
        self.soft_assert_equal(
            delegate.hosts,
            ["8.8.8.8", "1.1.1.1"],
            "Socket fallback should use only numeric addresses from one approved set",
        )
        self.soft_assert(
            all(
                timeout is not None and 0 <= timeout <= 5.0
                for timeout in delegate.timeouts
            ),
            "Address fallback should share the original connection timeout budget",
        )
        dns_delegate = _RecordingNetworkBackend()
        dns_backend = MCPNetworkBackend(delegate=dns_delegate)

        async def blocked_resolution(_hostname: str) -> tuple[str, ...]:
            await asyncio.Event().wait()
            return ("8.8.8.8",)

        with patch(
            "core.mcp.network.resolve_mcp_addresses",
            new=blocked_resolution,
        ):
            try:
                await dns_backend.connect_tcp(
                    "slow-dns.example",
                    443,
                    timeout=0.01,
                )
            except httpcore.ConnectTimeout:
                dns_timed_out = True
            else:
                dns_timed_out = False
        self.soft_assert(
            dns_timed_out and not dns_delegate.hosts,
            "DNS resolution must consume the socket connection timeout budget",
        )
        self.soft_assert_equal(
            (delegate.local_addresses, delegate.socket_options),
            (["0.0.0.0", "0.0.0.0"], [((1, 2, 3),), ((1, 2, 3),)]),
            "Socket policy should preserve local-address and socket options",
        )
        await stream.start_tls(
            httpcore.default_ssl_context(),
            server_hostname="public.example",
            timeout=5.0,
        )
        self.soft_assert_equal(
            delegate.stream.tls_hostnames,
            ["public.example"],
            "Numeric TCP pinning must preserve the original hostname for TLS SNI",
        )

        transport_delegate = _RecordingNetworkBackend()
        transport = MCPAsyncHTTPTransport(
            network_backend=MCPNetworkBackend(delegate=transport_delegate)
        )
        with patch(
            "core.mcp.network._resolve_addresses",
            return_value=("8.8.4.4",),
        ):
            async with httpx.AsyncClient(transport=transport) as client:
                response = await client.get("https://public.example/mcp")
        self.soft_assert_equal(
            (response.status_code, response.text, transport_delegate.hosts),
            (200, "ok", ["8.8.4.4"]),
            "HTTPX adapter should send a request through the policy-pinned address",
        )
        self.soft_assert_equal(
            transport_delegate.stream.tls_hostnames,
            ["public.example"],
            "The HTTPX/httpcore pool should retain the original hostname for TLS SNI",
        )

        rebound_delegate = _RecordingNetworkBackend()
        rebound_backend = MCPNetworkBackend(delegate=rebound_delegate)
        with patch(
            "core.mcp.network._resolve_addresses",
            side_effect=(("8.8.8.8",), ("169.254.169.254",)),
        ):
            await validate_mcp_endpoint(
                "https://public.example/mcp",
                allow_private_http=False,
            )
            try:
                await rebound_backend.connect_tcp("public.example", 443)
            except MCPNetworkPolicyError:
                socket_rebinding_rejected = True
            else:
                socket_rebinding_rejected = False
        self.soft_assert(
            socket_rebinding_rejected and not rebound_delegate.hosts,
            "Socket connect must reject a prohibited DNS change before dialing",
        )
        try:
            await backend.connect_unix_socket("/tmp/untrusted-mcp.sock")
        except MCPNetworkPolicyError:
            unix_socket_rejected = True
        else:
            unix_socket_rejected = False
        self.soft_assert(
            unix_socket_rejected,
            "MCP network policy must not admit Unix-domain socket bypasses",
        )

    async def _assert_private_http_connection(
        self,
        service: MCPConnectionService,
        owner: ExecutionAuthority,
        connection: MCPConnection,
    ) -> None:
        manager = MCPConnectionManager(connections=service, idle_timeout_seconds=0)
        with (
            patch(
                "core.mcp.network._resolve_addresses",
                return_value=("172.18.0.9",),
            ),
            patch("core.mcp.manager.Client", return_value=_ManagedTestClient()),
        ):
            lease = await manager.acquire(owner, connection)
            await lease.close()
        await manager.shutdown()


@dataclass(frozen=True)
class _TestTool:
    name: str


class _SuccessfulTestClient:
    async def __aenter__(self) -> _SuccessfulTestClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def list_tools(self, *, max_pages: int) -> list[_TestTool]:
        assert max_pages > 0
        return [_TestTool("search_messages"), _TestTool("archive_message")]


class _RejectedTestClient:
    async def __aenter__(self) -> _RejectedTestClient:
        request = httpx.Request("POST", "https://private.example/mcp")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError(
            "credential-bearing raw transport failure",
            request=request,
            response=response,
        )

    async def __aexit__(self, *_args: object) -> None:
        return None


class _MalformedTestClient:
    async def __aenter__(self) -> _MalformedTestClient:
        raise ValueError("malformed-secret-detail")

    async def __aexit__(self, *_args: object) -> None:
        return None


class _TimeoutTestClient:
    async def __aenter__(self) -> _TimeoutTestClient:
        raise TimeoutError

    async def __aexit__(self, *_args: object) -> None:
        return None


class _ManagedTestClient:
    def __init__(self) -> None:
        self.exit_count = 0

    async def __aenter__(self) -> _ManagedTestClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        self.exit_count += 1

    async def list_tools(self, *, max_pages: int) -> list[_TestTool]:
        assert max_pages > 0
        return [_TestTool("search_messages")]


class _BlockingManagedTestClient(_ManagedTestClient):
    def __init__(
        self,
        connect_started: asyncio.Event,
        finish_connect: asyncio.Event,
    ) -> None:
        super().__init__()
        self._connect_started = connect_started
        self._finish_connect = finish_connect

    async def list_tools(self, *, max_pages: int) -> list[_TestTool]:
        self._connect_started.set()
        await self._finish_connect.wait()
        return await super().list_tools(max_pages=max_pages)


class _InvalidatingManagedTestClient(_ManagedTestClient):
    def __init__(self, invalidate: Callable[[], None]) -> None:
        super().__init__()
        self._invalidate = invalidate

    async def list_tools(self, *, max_pages: int) -> list[_TestTool]:
        self._invalidate()
        return await super().list_tools(max_pages=max_pages)


class _RecordingNetworkStream(httpcore.AsyncNetworkStream):
    def __init__(self) -> None:
        self.tls_hostnames: list[str | None] = []
        self._response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        del timeout
        result = self._response[:max_bytes]
        self._response = self._response[max_bytes:]
        return result

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        del buffer, timeout

    async def aclose(self) -> None:
        return None

    async def start_tls(
        self,
        ssl_context: object,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del ssl_context, timeout
        self.tls_hostnames.append(server_hostname)
        return self


class _RecordingNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, *, failures: int = 0) -> None:
        self.failures = failures
        self.hosts: list[str] = []
        self.timeouts: list[float | None] = []
        self.local_addresses: list[str | None] = []
        self.socket_options: list[tuple[SocketOption, ...]] = []
        self.stream = _RecordingNetworkStream()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del port
        self.hosts.append(host)
        self.timeouts.append(timeout)
        self.local_addresses.append(local_address)
        self.socket_options.append(tuple(socket_options or ()))
        if self.failures:
            self.failures -= 1
            raise httpcore.ConnectError("injected connection failure")
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SocketOption] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise AssertionError("Unix socket delegate must not be called")

    async def sleep(self, seconds: float) -> None:
        del seconds


async def _allow_endpoint(*_args: object, **_kwargs: object) -> None:
    return None


if __name__ == "__main__":
    asyncio.run(MCPConnectionIsolationScenario().test_scenario())
