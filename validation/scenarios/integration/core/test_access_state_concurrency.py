"""Real transactions and OAuth exchanges fence stale access-state operations."""

from __future__ import annotations

import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.access_state_concurrency import (
    guarded_write_in_thread,
    reject_secret_cleanup,
)
from validation.scenarios.support.google_connections import grant
from validation.scenarios.support.oauth_http_inputs import (
    google_oauth_server,
    google_setup,
    start_google,
)

pytestmark = pytest.mark.scenario


async def test_guarded_secret_write_cannot_cross_concurrent_guard_rotation(
    app: Application,
) -> None:
    app.evidence.register_secret(
        "old-guard", "new-guard", "current-value", "stale-write"
    )
    await app.start()
    from core.identity import ExecutionAuthority
    from core.secrets import (
        SecretGuardMismatchError,
        SecretIdentity,
        get_encrypted_secrets_service,
    )

    service = get_encrypted_secrets_service()
    owner = ExecutionAuthority("guard-owner")
    guard, target = SecretIdentity("access-test", "generation"), SecretIdentity(
        "access-test", "value"
    )
    service.set_for_authority(owner, guard.namespace, guard.name, "old-guard")
    service.set_for_authority(owner, target.namespace, target.name, "current-value")
    with sqlite3.connect(app.root / "system/access.db", timeout=10) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        service.set_for_authority_on_connection(
            connection, owner, guard.namespace, guard.name, "new-guard"
        )
        started = Event()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                guarded_write_in_thread, service, owner, guard, target, started
            )
            assert started.wait(5) and not future.done()
            connection.commit()
            with pytest.raises(SecretGuardMismatchError):
                await asyncio.to_thread(future.result, 10)
    assert service.get_for_authority(owner, guard.namespace, guard.name) == "new-guard"
    assert (
        service.get_for_authority(owner, target.namespace, target.name)
        == "current-value"
    )


async def test_google_disconnect_cleanup_rolls_back_then_retries(
    app: Application,
) -> None:
    app.evidence.register_secret("client-secret")
    await app.start()
    from core.connections import GoogleConnectionCreate
    from core.identity import ExecutionAuthority
    from core.runtime.state import get_runtime_context

    owner = ExecutionAuthority("disconnect-owner")
    runtime = get_runtime_context()
    google = runtime.google_connection
    assert google is not None
    connection = runtime.built_in_connections.create_google_connection_for_authority(
        owner,
        GoogleConnectionCreate(
            display_name="Google", client_id="client.apps.googleusercontent.com"
        ),
    )
    google.set_client_secret(owner, "client-secret", connection.connection_id)
    token = grant(app, "disconnect-owner")
    google.save_token_state(owner, token, connection.connection_id)
    before = runtime.built_in_connections.get_google_connection_for_authority(
        owner, connection.connection_id
    )
    credential = google.resolve_client_credential(owner, connection.connection_id)
    with reject_secret_cleanup(app), pytest.raises(sqlite3.IntegrityError):
        google.clear_token_state(owner, connection.connection_id)
    assert (
        runtime.built_in_connections.get_google_connection_for_authority(
            owner, connection.connection_id
        )
        == before
    )
    assert (
        google.resolve_client_credential(owner, connection.connection_id) == credential
    )
    assert google.load_token_state(owner, connection.connection_id) == token
    google.clear_token_state(owner, connection.connection_id)
    assert google.load_token_state(owner, connection.connection_id) is None
    assert (
        google.resolve_client_secret(owner, connection.connection_id) == "client-secret"
    )


async def test_google_inflight_completion_cannot_restore_deleted_connection(
    app: Application,
) -> None:
    await app.start()
    with google_oauth_server(app) as server:
        owner, connection, google, connections, coordinator = google_setup(app, server)
        _, query = start_google(app, coordinator, owner, connection)
        server.block = "token"
        task = asyncio.create_task(
            coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="deleted-connection-code",
                state=query["state"][0],
            )
        )
        try:
            await server.wait_entered()
            assert google.delete_connection(owner, connection.connection_id)
            server.release.set()
            with pytest.raises(ValueError, match="Google connection is not configured"):
                await task
            assert (
                connections.get_google_connection_for_authority(
                    owner, connection.connection_id
                )
                is None
            )
            with sqlite3.connect(app.root / "system/access.db") as conn:
                count = conn.execute(
                    "SELECT count(*) FROM encrypted_secrets WHERE owner_principal_id = ? AND namespace = ?",
                    (owner.principal_id, f"oauth.google.{connection.connection_id}"),
                ).fetchone()[0]
            assert count == 0
        finally:
            server.release.set()
            await asyncio.gather(task, return_exceptions=True)


async def test_superseded_google_attempt_cannot_consume_new_attempt(
    app: Application,
) -> None:
    await app.start()
    from core.integrations.google import GoogleOAuthError

    with google_oauth_server(app) as server:
        owner, connection, google, _, coordinator = google_setup(app, server)
        _, first = start_google(app, coordinator, owner, connection)
        _, newer = start_google(app, coordinator, owner, connection)
        assert first["state"] != newer["state"]
        with pytest.raises(GoogleOAuthError):
            await coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="old-code",
                state=first["state"][0],
            )
        assert server.requests == []
        result = await coordinator.complete(
            authority=owner,
            connection_id=connection.connection_id,
            code="new-code",
            state=newer["state"][0],
        )
        assert google.load_token_state(owner, connection.connection_id) == result
        with pytest.raises(GoogleOAuthError):
            await coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="replay-code",
                state=newer["state"][0],
            )


async def test_new_google_authorization_survives_older_inflight_completion(
    app: Application,
) -> None:
    await app.start()
    from core.integrations.google import GoogleOAuthError

    with google_oauth_server(app) as server:
        owner, connection, google, _, coordinator = google_setup(app, server)
        _, first = start_google(app, coordinator, owner, connection)
        server.block = "token"
        task = asyncio.create_task(
            coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="first-code",
                state=first["state"][0],
            )
        )
        try:
            await server.wait_entered()
            _, newer = start_google(app, coordinator, owner, connection)
            assert first["state"] != newer["state"]
            server.release.set()
            await task
            with pytest.raises(GoogleOAuthError):
                await coordinator.complete(
                    authority=owner,
                    connection_id=connection.connection_id,
                    code="first-replay",
                    state=first["state"][0],
                )
            second = await coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="second-code",
                state=newer["state"][0],
            )
            assert google.load_token_state(owner, connection.connection_id) == second
            with pytest.raises(GoogleOAuthError):
                await coordinator.complete(
                    authority=owner,
                    connection_id=connection.connection_id,
                    code="second-replay",
                    state=newer["state"][0],
                )
        finally:
            server.release.set()
            await asyncio.gather(task, return_exceptions=True)


async def test_mcp_stale_grant_cannot_cross_concurrent_credential_rotation(
    app: Application,
) -> None:
    app.evidence.register_secret(
        "mcp-old-secret", "mcp-new-secret", "mcp-stale-grant", "mcp-current-grant"
    )
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.mcp import MCPAuthMode, MCPConnectionCreate
    from core.runtime.state import get_runtime_context
    from core.secrets import SecretGuardMismatchError

    service = get_runtime_context().mcp_connections
    assert service is not None
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        connection = service.create_connection(
            MCPConnectionCreate(
                display_name="Concurrent OAuth",
                url="https://mcp.example.test",
                auth_mode=MCPAuthMode.OAUTH,
            )
        )
        service.set_oauth_client_secret(connection.connection_id, "mcp-old-secret")
        stale = service.oauth_storage(LOCAL_USER_AUTHORITY, connection.connection_id)
        entered, release = Event(), Event()

        def stale_write():
            entered.set()
            assert release.wait(10)
            stale.put_sync("grant", {"token": "mcp-stale-grant"})

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(stale_write)
            try:
                assert entered.wait(5)
                service.set_oauth_client_secret(
                    connection.connection_id, "mcp-new-secret"
                )
                current = service.oauth_storage(
                    LOCAL_USER_AUTHORITY, connection.connection_id
                )
                current.put_sync("grant", {"token": "mcp-current-grant"})
            finally:
                release.set()
            with pytest.raises(SecretGuardMismatchError):
                await asyncio.to_thread(future.result, 10)
        assert current.get_sync("grant") == {"token": "mcp-current-grant"}
        assert (
            service.resolve_oauth_client_secret(
                LOCAL_USER_AUTHORITY, connection.connection_id
            )
            == "mcp-new-secret"
        )
