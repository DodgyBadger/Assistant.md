"""MCP OAuth resumes controlled encrypted pending input through real HTTP."""

from __future__ import annotations

import sqlite3

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.mcp_oauth_inputs import (
    activity,
    mcp_token_server,
    pending_input,
    reject_revocation,
)

pytestmark = pytest.mark.scenario


async def test_mcp_oauth_durable_resume_rejects_wrong_state_and_encrypts_grant(
    app: Application,
):
    await app.start()
    from core.identity import ExecutionAuthority
    from core.mcp import MCPAuthMode, MCPConnectionCreate, MCPConnectionManager
    from core.mcp.oauth import MCPOAuthCoordinator, MCPOAuthError
    from core.runtime.state import get_runtime_context

    service = get_runtime_context().mcp_connections
    owner, other = ExecutionAuthority("oauth-owner"), ExecutionAuthority("oauth-other")
    with mcp_token_server(app) as server:
        connection = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Resume OAuth",
                url=f"http://127.0.0.1:{server.server_port}/mcp",
                allow_private_http=True,
                auth_mode=MCPAuthMode.OAUTH,
            ),
        )
        manager = MCPConnectionManager(connections=service)
        first = MCPOAuthCoordinator(connections=service, manager=manager)
        try:
            await pending_input(service, owner, connection, server)
            assert (
                await first.status(
                    authority=owner, connection_id=connection.connection_id
                )
            ).status == "pending"
            with pytest.raises(LookupError):
                await first.status(
                    authority=other, connection_id=connection.connection_id
                )
            await first.shutdown()
            resumed = MCPOAuthCoordinator(connections=service, manager=manager)
            try:
                assert (
                    await resumed.status(
                        authority=owner, connection_id=connection.connection_id
                    )
                ).status == "pending"
                with pytest.raises(MCPOAuthError):
                    await resumed.complete(
                        authority=owner,
                        connection_id=connection.connection_id,
                        code="validation-code",
                        state="wrong-state",
                    )
                assert not server.requests
                assert (
                    await resumed.status(
                        authority=owner, connection_id=connection.connection_id
                    )
                ).status == "pending"
                completed = await resumed.complete(
                    authority=owner,
                    connection_id=connection.connection_id,
                    code="validation-code",
                    state="validation-state",
                )
                assert (
                    completed.connected and completed.operation_id == "resume-operation"
                )
                assert (
                    await resumed.status(
                        authority=owner, connection_id=connection.connection_id
                    )
                ).status == "connected"
                assert server.requests[0]["code_verifier"] == [
                    "validation-code-verifier"
                ]
                stored = (app.root / "system/access.db").read_bytes()
                assert (
                    b"validation-access-token" not in stored
                    and b"validation-refresh-token" not in stored
                )
                with pytest.raises(MCPOAuthError):
                    await resumed.complete(
                        authority=owner,
                        connection_id=connection.connection_id,
                        code="validation-code",
                        state="validation-state",
                    )
                await resumed.disconnect(
                    authority=owner, connection_id=connection.connection_id
                )
                assert (
                    await resumed.status(
                        authority=owner, connection_id=connection.connection_id
                    )
                ).status == "disconnected"
            finally:
                await resumed.shutdown()
        finally:
            await first.shutdown()
            await manager.shutdown()


async def test_mcp_oauth_resume_failure_is_correlated_and_hides_provider_credentials(
    app: Application,
):
    await app.start()
    from core.identity import ExecutionAuthority
    from core.mcp import MCPAuthMode, MCPConnectionCreate, MCPConnectionManager
    from core.mcp.oauth import MCPOAuthCoordinator, MCPOAuthError
    from core.runtime.state import get_runtime_context

    service = get_runtime_context().mcp_connections
    owner = ExecutionAuthority("oauth-owner")
    with mcp_token_server(app) as server:
        server.malformed = True
        connection = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Reject OAuth",
                url=f"http://127.0.0.1:{server.server_port}/mcp",
                allow_private_http=True,
                auth_mode=MCPAuthMode.OAUTH,
            ),
        )
        manager = MCPConnectionManager(connections=service)
        coordinator = MCPOAuthCoordinator(connections=service, manager=manager)
        try:
            await pending_input(
                service, owner, connection, server, operation="failed-operation"
            )
            with pytest.raises(MCPOAuthError):
                await coordinator.complete(
                    authority=owner,
                    connection_id=connection.connection_id,
                    code="validation-code",
                    state="validation-state",
                )
            failure = next(
                row
                for row in activity(app)
                if row.get("event") == "mcp_oauth_attempt_failed"
                and row.get("operation_id") == "failed-operation"
            )
            assert (
                failure["connection_id"],
                failure["phase"],
                failure["status"],
                failure["error_type"],
            ) == (
                connection.connection_id,
                "token_exchange",
                "failed",
                "ValidationError",
            )
            assert failure["error"] == "The MCP server rejected OAuth completion."
            raw = (app.root / "system/activity.log").read_text()
            assert (
                "PRIVATE_OAUTH_CREDENTIAL" not in raw
                and "PRIVATE_REFRESH_CREDENTIAL" not in raw
            )
            await pending_input(
                service, owner, connection, server, operation="disconnect-operation"
            )
            with reject_revocation(app):
                with pytest.raises(sqlite3.IntegrityError):
                    await coordinator.disconnect(
                        authority=owner, connection_id=connection.connection_id
                    )
            assert not any(
                row.get("event") == "mcp_oauth_attempt_cancelled"
                and row.get("operation_id") == "disconnect-operation"
                for row in activity(app)
            )
            await coordinator.disconnect(
                authority=owner, connection_id=connection.connection_id
            )
            cancelled = [
                row
                for row in activity(app)
                if row.get("event") == "mcp_oauth_attempt_cancelled"
                and row.get("operation_id") == "disconnect-operation"
            ]
            assert len(cancelled) == 1 and cancelled[0]["phase"] == "disconnect"
        finally:
            await coordinator.shutdown()
            await manager.shutdown()
