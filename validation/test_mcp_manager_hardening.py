"""Focused lifecycle tests for retained MCP connection management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import cast

import pytest
from fastmcp import Client
from mcp.types import Tool

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = Path("/tmp/assistantmd-mcp-manager-hardening-tests")
(_TEST_ROOT / "data").mkdir(parents=True, exist_ok=True)
(_TEST_ROOT / "system").mkdir(parents=True, exist_ok=True)
set_bootstrap_roots(_TEST_ROOT / "data", _TEST_ROOT / "system")

import core.mcp.manager as manager_module  # noqa: E402
import core.settings as settings  # noqa: E402
from core.advanced_shell.preflight import (  # noqa: E402
    AdvancedShellPreflightSnapshot,
    AdvancedShellReadiness,
)
from core.identity import LOCAL_USER_AUTHORITY, ExecutionAuthority  # noqa: E402
from core.mcp import (  # noqa: E402
    MCPAuthMode,
    MCPConnection,
    MCPConnectionLease,
    MCPConnectionManager,
    MCPConnectionService,
    MCPStdioConfig,
    MCPTransport,
)
from core.tools.advanced_shell import ShellTransportConfig  # noqa: E402


class _ConnectionSource:
    def __init__(self, connections: tuple[MCPConnection, ...]) -> None:
        self.connections = connections

    def list_connections_for_authority(
        self, authority: ExecutionAuthority
    ) -> list[MCPConnection]:
        del authority
        return list(self.connections)

    def get_connection_for_authority(
        self, authority: ExecutionAuthority, connection_id: str
    ) -> MCPConnection | None:
        del authority
        return next(
            (
                connection
                for connection in self.connections
                if connection.connection_id == connection_id
            ),
            None,
        )

    def resolve_credential(
        self,
        authority: ExecutionAuthority,
        connection_id: str,
        *,
        expected_connection: MCPConnection | None = None,
    ) -> None:
        del authority, connection_id
        return None

    def oauth_storage(
        self,
        authority: ExecutionAuthority,
        connection_id: str,
        *,
        expected_connection: MCPConnection | None = None,
    ) -> None:
        del authority, connection_id
        return None


def _connection(connection_id: str) -> MCPConnection:
    return MCPConnection(
        connection_id=connection_id,
        slug=connection_id,
        display_name=connection_id,
        url=f"https://{connection_id}.example/mcp",
        transport=MCPTransport.STREAMABLE_HTTP,
        auth_mode=MCPAuthMode.NONE,
        header_name=None,
        enabled=True,
        allow_private_http=False,
        allowed_tools=None,
        credential_present=False,
        config_version=1,
        created_at="2026-09-03T00:00:00Z",
        updated_at="2026-09-03T00:00:00Z",
    )


def _stdio_connection(connection_id: str) -> MCPConnection:
    return replace(
        _connection(connection_id),
        url=None,
        transport=MCPTransport.ADVANCED_SHELL_STDIO,
        stdio=MCPStdioConfig(
            executable="node",
            arguments=("provider.js",),
            working_directory="/workspace",
        ),
    )


def _shell_transport(tmp_path: Path) -> ShellTransportConfig:
    private_key = tmp_path / "client_key"
    known_hosts = tmp_path / "known_hosts"
    private_key.touch()
    known_hosts.touch()
    return ShellTransportConfig(
        host="advanced-shell",
        private_key_path=private_key,
        known_hosts_path=known_hosts,
    )


async def _ready_shell() -> AdvancedShellPreflightSnapshot:
    return AdvancedShellPreflightSnapshot(
        state=AdvancedShellReadiness.READY,
        message="Ready.",
    )


class _ControlledClient:
    enter_gates: list[asyncio.Event] = []
    entered = 0
    active_enters = 0
    max_active_enters = 0

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.index = len(type(self).enter_gates)
        type(self).enter_gates.append(asyncio.Event())

    @classmethod
    def reset(cls) -> None:
        cls.enter_gates = []
        cls.entered = 0
        cls.active_enters = 0
        cls.max_active_enters = 0

    async def __aenter__(self) -> _ControlledClient:
        cls = type(self)
        cls.entered += 1
        cls.active_enters += 1
        cls.max_active_enters = max(cls.max_active_enters, cls.active_enters)
        try:
            await cls.enter_gates[self.index].wait()
        finally:
            cls.active_enters -= 1
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def list_tools(self, **_kwargs: object) -> list[Tool]:
        return []


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_snapshot_cancellation_releases_completed_acquires() -> None:
    first, blocked = _connection("first"), _connection("blocked")
    manager = MCPConnectionManager(
        connections=cast(MCPConnectionService, _ConnectionSource((first, blocked)))
    )
    first_acquired = asyncio.Event()
    never_finish = asyncio.Event()
    release_count = 0

    async def release() -> None:
        nonlocal release_count
        release_count += 1

    async def acquire(
        _manager: MCPConnectionManager,
        authority: ExecutionAuthority,
        connection: MCPConnection,
    ) -> MCPConnectionLease:
        del authority
        if connection.connection_id == blocked.connection_id:
            await never_finish.wait()
        lease = MCPConnectionLease(
            connection=connection,
            client=cast(Client, object()),
            tools=(Tool(name="ready", inputSchema={"type": "object"}),),
            release=release,
        )
        first_acquired.set()
        return lease

    manager.acquire = MethodType(acquire, manager)
    task = asyncio.create_task(manager.acquire_snapshot(LOCAL_USER_AUTHORITY))
    await first_acquired.wait()
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert release_count == 1
    await manager.shutdown()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [(None, 4), ("invalid", 4), (0, 1), (7, 7), (100, 32)],
)
def test_managed_stdio_launch_limit_is_configurable_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
    configured: object,
    expected: int,
) -> None:
    entries = (
        {}
        if configured is None
        else {
            "mcp_max_concurrent_advanced_shell_stdio_launches": SimpleNamespace(
                value=configured
            )
        }
    )
    monkeypatch.setattr(settings, "get_general_settings", lambda: entries)

    assert settings.get_mcp_max_concurrent_advanced_shell_stdio_launches() == expected


@pytest.mark.asyncio
async def test_manager_rejects_non_authoritative_connection_fields() -> None:
    authoritative = _connection("owned")
    manager = MCPConnectionManager(
        connections=cast(MCPConnectionService, _ConnectionSource((authoritative,)))
    )
    forged = replace(authoritative, url="https://substituted.example/mcp")

    with pytest.raises(RuntimeError, match="configuration is not current"):
        await manager.acquire(LOCAL_USER_AUTHORITY, forged)

    await manager.shutdown()


@pytest.mark.asyncio
async def test_stdio_launch_semaphore_serializes_and_recovers_after_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first = _stdio_connection("first")
    second = _stdio_connection("second")
    third = _stdio_connection("third")
    source = _ConnectionSource((first, second, third))
    manager = MCPConnectionManager(
        connections=cast(MCPConnectionService, source),
        advanced_shell_stdio=_shell_transport(tmp_path),
        advanced_shell_readiness=_ready_shell,
        max_concurrent_stdio_launches=1,
    )
    _ControlledClient.reset()
    monkeypatch.setattr(manager_module, "Client", _ControlledClient)

    first_task = asyncio.create_task(
        manager._connect_advanced_shell_stdio(LOCAL_USER_AUTHORITY, first)
    )
    await _wait_until(lambda: _ControlledClient.entered == 1)
    second_task = asyncio.create_task(
        manager._connect_advanced_shell_stdio(LOCAL_USER_AUTHORITY, second)
    )
    await asyncio.sleep(0)
    assert _ControlledClient.entered == 1

    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task
    await _wait_until(lambda: _ControlledClient.entered == 2)
    assert _ControlledClient.max_active_enters == 1

    _ControlledClient.enter_gates[1].set()
    await second_task

    third_task = asyncio.create_task(
        manager._connect_advanced_shell_stdio(LOCAL_USER_AUTHORITY, third)
    )
    await _wait_until(lambda: _ControlledClient.entered == 3)
    _ControlledClient.enter_gates[2].set()
    await third_task
    await manager.shutdown()


@pytest.mark.asyncio
async def test_http_connection_does_not_consume_stdio_launch_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection("http")
    manager = MCPConnectionManager(
        connections=cast(MCPConnectionService, _ConnectionSource((connection,))),
        max_concurrent_stdio_launches=1,
    )

    class _ImmediateClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _ImmediateClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def list_tools(self, **_kwargs: object) -> list[Tool]:
            return []

    async def allow_endpoint(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(manager_module, "Client", _ImmediateClient)
    monkeypatch.setattr(manager_module, "validate_mcp_endpoint", allow_endpoint)
    await manager._stdio_launch_semaphore.acquire()
    try:
        entry = await manager._connect(LOCAL_USER_AUTHORITY, connection)
    finally:
        manager._stdio_launch_semaphore.release()

    assert entry.connection is connection
    await entry.client.__aexit__(None, None, None)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_client_cleanup_failure_logs_sanitized_connection_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _stdio_connection("cleanup-context")
    records: list[dict[str, object]] = []

    class _FailingClient:
        async def __aexit__(self, *_args: object) -> None:
            raise ValueError("provider stderr must not be logged")

    def capture_warning(
        _message: str,
        *,
        data: dict[str, object],
        **_fields: object,
    ) -> None:
        records.append(data)

    monkeypatch.setattr(manager_module.logger, "warning", capture_warning)
    await manager_module._close_client(
        cast(Client, _FailingClient()),
        connection=connection,
    )

    assert records == [
        {
            "event": "mcp_client_cleanup_failed",
            "error_type": "ValueError",
            "connection_id": connection.connection_id,
            "transport": connection.transport.value,
        }
    ]


@pytest.mark.asyncio
async def test_invalidation_acknowledges_marking_and_preserves_active_lease() -> None:
    connection = _connection("acknowledged")
    manager = MCPConnectionManager(
        connections=cast(MCPConnectionService, _ConnectionSource((connection,)))
    )
    closes = 0

    class _Client:
        async def __aexit__(self, *_args: object) -> None:
            nonlocal closes
            closes += 1

    key = (LOCAL_USER_AUTHORITY.principal_id, connection.connection_id, 1)
    entry = manager_module._ManagedConnection(
        connection, cast(Client, _Client()), (), 1, 0
    )
    manager._entries[key] = entry
    lease = manager._lease(key, entry)
    manager.invalidate(LOCAL_USER_AUTHORITY.principal_id, connection.connection_id)
    assert entry.invalidated  # No event-loop yield is needed for acknowledgement.
    assert closes == 0
    await lease.close()
    assert closes == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_worker_invalidation_acknowledges_and_propagates_marking_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = MCPConnectionManager(
        connections=cast(MCPConnectionService, _ConnectionSource(()))
    )
    target = (LOCAL_USER_AUTHORITY.principal_id, "worker")
    await asyncio.to_thread(manager.invalidate, *target)
    assert manager._invalidation_epochs[target] == 1

    def fail(*_args: object) -> None:
        raise RuntimeError("injected marking failure")

    monkeypatch.setattr(manager, "_spawn_invalidation", fail)
    with pytest.raises(RuntimeError, match="injected marking failure"):
        await asyncio.to_thread(manager.invalidate, *target)
    await manager.shutdown()


@pytest.mark.asyncio
async def test_invalidation_cleanup_failure_is_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection("cleanup")
    manager = MCPConnectionManager(
        connections=cast(MCPConnectionService, _ConnectionSource((connection,)))
    )
    key = (LOCAL_USER_AUTHORITY.principal_id, connection.connection_id, 1)
    manager._entries[key] = manager_module._ManagedConnection(
        connection, cast(Client, object()), (), 0, 0
    )
    records: list[dict[str, object]] = []

    async def fail(_entries: object) -> None:
        raise RuntimeError("sensitive provider message")

    monkeypatch.setattr(manager, "_close_invalidated", fail)
    monkeypatch.setattr(
        manager_module.logger,
        "warning",
        lambda _message, *, data: records.append(data),
    )
    manager.invalidate(LOCAL_USER_AUTHORITY.principal_id, connection.connection_id)
    assert key not in manager._entries
    await _wait_until(lambda: bool(records))
    assert records == [
        {"event": "mcp_invalidation_cleanup_failed", "error_type": "RuntimeError"}
    ]
    await manager.shutdown()


@pytest.mark.asyncio
async def test_connection_captures_bound_credential_before_endpoint_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = replace(_connection("old-endpoint"), auth_mode=MCPAuthMode.BEARER)
    secret = "old-secret"

    class _Source(_ConnectionSource):
        def resolve_credential(
            self,
            authority: ExecutionAuthority,
            connection_id: str,
            *,
            expected_connection: MCPConnection | None = None,
        ) -> str:
            assert expected_connection == self.connections[0]
            return secret

    source = _Source((connection,))
    manager = MCPConnectionManager(connections=cast(MCPConnectionService, source))

    async def mutate_during_resolution(*_args: object, **_kwargs: object) -> None:
        nonlocal secret
        source.connections = (
            replace(connection, config_version=2, url="https://new.example/"),
        )
        secret = "new-secret"

    def check_material(candidate: MCPConnection, credential: str, **_kwargs: object):
        assert candidate.url == connection.url
        assert credential == "old-secret"
        return None, credential

    class _Client:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def list_tools(self, **_kwargs: object) -> list[Tool]:
            return []

    monkeypatch.setattr(
        manager_module, "validate_mcp_endpoint", mutate_during_resolution
    )
    monkeypatch.setattr(manager_module, "_build_auth", check_material)
    monkeypatch.setattr(manager_module, "Client", _Client)
    await manager._connect(LOCAL_USER_AUTHORITY, connection)
    await manager.shutdown()
