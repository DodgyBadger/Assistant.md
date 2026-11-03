# Deferred reference only; see validation/registry/pytest-gaps.json.
# Original complete source is available in Git before the 2026-10-31 cleanup.
"""Focused lifecycle tests for retained MCP connection management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from fastmcp import Client

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = Path("/tmp/assistantmd-mcp-manager-hardening-tests")
(_TEST_ROOT / "data").mkdir(parents=True, exist_ok=True)
(_TEST_ROOT / "system").mkdir(parents=True, exist_ok=True)
set_bootstrap_roots(_TEST_ROOT / "data", _TEST_ROOT / "system")

import core.mcp.manager as manager_module  # noqa: E402
from core.identity import LOCAL_USER_AUTHORITY, ExecutionAuthority  # noqa: E402
from core.mcp import (  # noqa: E402
    MCPAuthMode,
    MCPConnection,
    MCPConnectionManager,
    MCPConnectionService,
    MCPStdioConfig,
    MCPTransport,
)


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


async def _wait_until(predicate: Callable[[], bool]) -> None:
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0)


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

    assert len(records) == 1
    assert records[0].get("error")
    assert "provider stderr must not be logged" not in repr(records)
    assert [{key: value for key, value in records[0].items() if key != "error"}] == [
        {
            "event": "mcp_client_cleanup_failed",
            "status": "failed",
            "error_type": "ValueError",
            "connection_id": connection.connection_id,
            "transport": connection.transport.value,
            "issue": connection.connection_id,
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
    assert len(records) == 1
    assert records[0].get("error")
    assert "sensitive provider message" not in repr(records)
    assert [{key: value for key, value in records[0].items() if key != "error"}] == [
        {
            "event": "mcp_invalidation_cleanup_failed",
            "status": "failed",
            "error_type": "RuntimeError",
            "issue": "mcp_invalidation_cleanup",
        }
    ]
    await manager.shutdown()
