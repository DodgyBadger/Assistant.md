"""Deterministic MCP OAuth completion races against durable mutations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = Path("/tmp/assistantmd-mcp-oauth-hardening-tests")
(_TEST_ROOT / "data").mkdir(parents=True, exist_ok=True)
(_TEST_ROOT / "system").mkdir(parents=True, exist_ok=True)
set_bootstrap_roots(_TEST_ROOT / "data", _TEST_ROOT / "system")

from fastmcp.client.auth.oauth import TokenStorageAdapter  # noqa: E402
from mcp.shared.auth import OAuthToken  # noqa: E402

import core.mcp.oauth as oauth_module  # noqa: E402
from api.exceptions import APIException  # noqa: E402
from api.models import MCPOAuthCompleteRequest  # noqa: E402
from api.services import mcp as mcp_api  # noqa: E402
from core.identity import LOCAL_USER_AUTHORITY as OWNER  # noqa: E402
from core.identity import use_execution_authority  # noqa: E402
from core.mcp import (  # noqa: E402
    MCPAuthMode,
    MCPConnectionCreate,
    MCPConnectionManager,
    MCPConnectionService,
    MCPTransport,
)
from core.mcp.oauth import MCPOAuthCoordinator, MCPOAuthError, _Attempt  # noqa: E402
from core.secrets import EncryptedSecretsService, SecretKeyring  # noqa: E402


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ["disconnect", "replace", "delete", "new-attempt", "shutdown"]
)
async def test_persisted_completion_cannot_revive_changed_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    service = MCPConnectionService(
        system_root=str(tmp_path),
        secrets=EncryptedSecretsService(
            system_root=str(tmp_path),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        ),
    )
    connection = service.create_connection_for_authority(
        OWNER,
        MCPConnectionCreate(
            display_name="Completion race",
            url="https://mcp.example.test/",
            transport=MCPTransport.STREAMABLE_HTTP,
            auth_mode=MCPAuthMode.OAUTH,
        ),
    )
    pending = {
        "state": "attempt-a",
        "code_verifier": "verifier",
        "redirect_uri": "http://localhost/callback",
        "token_endpoint": "https://mcp.example.test/token",
        "client_id": "client-a",
        "token_endpoint_auth_method": "none",
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    }
    service.oauth_storage(OWNER, connection.connection_id).put_sync(
        "pending-authorization", pending, collection="assistantmd"
    )
    entered, release = asyncio.Event(), asyncio.Event()
    requests = 0

    async def respond(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        entered.set()
        await release.wait()
        return httpx.Response(
            200, json={"access_token": "stale-token", "token_type": "Bearer"}
        )

    monkeypatch.setattr(
        oauth_module,
        "mcp_oauth_http_client_factory",
        lambda **_kwargs: lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    manager = MCPConnectionManager(connections=service)
    coordinator = MCPOAuthCoordinator(connections=service, manager=manager)
    task = asyncio.create_task(
        coordinator.complete(
            authority=OWNER,
            connection_id=connection.connection_id,
            code="code-a",
            state="attempt-a",
        )
    )
    try:
        async with asyncio.timeout(2):
            await entered.wait()
        # A second completion must reject the consumed attempt before HTTP.
        with pytest.raises(MCPOAuthError):
            async with asyncio.timeout(2):
                await coordinator.complete(
                    authority=OWNER,
                    connection_id=connection.connection_id,
                    code="code-a",
                    state="attempt-a",
                )
        assert requests == 1
        with use_execution_authority(OWNER):
            if mutation == "shutdown":
                await coordinator.shutdown()
            elif mutation == "delete":
                service.delete_connection(connection.connection_id)
            elif mutation == "replace":
                service.set_oauth_client_secret(connection.connection_id, "new-secret")
            else:
                service.disconnect_oauth(OWNER, connection.connection_id)
                if mutation == "new-attempt":
                    service.oauth_storage(OWNER, connection.connection_id).put_sync(
                        "pending-authorization",
                        {**pending, "state": "attempt-b"},
                        collection="assistantmd",
                    )
        release.set()
        with pytest.raises(
            asyncio.CancelledError if mutation == "shutdown" else MCPOAuthError
        ):
            await task
        if mutation != "delete":
            status = await coordinator.status(
                authority=OWNER, connection_id=connection.connection_id
            )
            assert not status.connected
            assert status.status == (
                "pending" if mutation == "new-attempt" else "disconnected"
            )
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await coordinator.shutdown()
        await manager.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["local", "persisted", "disconnect"])
async def test_saved_oauth_state_survives_failed_runtime_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    service = MCPConnectionService(
        system_root=str(tmp_path),
        secrets=EncryptedSecretsService(
            system_root=str(tmp_path),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        ),
    )
    connection = service.create_connection_for_authority(
        OWNER,
        MCPConnectionCreate(
            display_name="Committed OAuth",
            url="https://mcp.example.test/",
            auth_mode=MCPAuthMode.OAUTH,
        ),
    )

    class _Coordinator(MCPOAuthCoordinator):
        async def _run_attempt(self, attempt: _Attempt) -> None:
            attempt.storage.put_sync(
                "pending-authorization",
                {
                    "state": "attempt-a",
                    "code_verifier": "verifier",
                    "redirect_uri": attempt.redirect_uri,
                    "token_endpoint": "https://mcp.example.test/token",
                    "client_id": "client-a",
                    "token_endpoint_auth_method": "none",
                    "expires_at": (
                        datetime.now(UTC) + timedelta(minutes=5)
                    ).isoformat(),
                },
                collection="assistantmd",
            )
            attempt.authorization_url.set_result(
                "https://identity.example/?state=attempt-a"
            )
            await attempt.callback
            await TokenStorageAdapter(
                async_key_value=attempt.storage,
                server_url=connection.require_url(),
            ).set_tokens(
                OAuthToken(access_token="saved-oauth-token", token_type="Bearer")
            )

    manager = MCPConnectionManager(connections=service)
    coordinator: MCPOAuthCoordinator = _Coordinator(
        connections=service, manager=manager
    )
    await coordinator.start(
        authority=OWNER,
        connection_id=connection.connection_id,
        redirect_uri="http://localhost/callback",
    )
    if mode == "disconnect":
        await TokenStorageAdapter(
            async_key_value=service.oauth_storage(OWNER, connection.connection_id),
            server_url=connection.require_url(),
        ).set_tokens(OAuthToken(access_token="saved-oauth-token", token_type="Bearer"))
    if mode == "persisted":
        await coordinator.shutdown()
        coordinator = MCPOAuthCoordinator(connections=service, manager=manager)
        monkeypatch.setattr(
            oauth_module,
            "mcp_oauth_http_client_factory",
            lambda **_kwargs: lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _request: httpx.Response(
                        200,
                        json={
                            "access_token": "saved-oauth-token",
                            "token_type": "Bearer",
                        },
                    )
                ),
                **kwargs,
            ),
        )
    records: list[dict[str, object]] = []

    def fail(*_args: object) -> None:
        raise RuntimeError("sensitive runtime fixture error")

    monkeypatch.setattr(manager, "invalidate", fail)
    monkeypatch.setattr(mcp_api, "_oauth_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        oauth_module.logger,
        "error",
        lambda _message, *, data: records.append(data),
    )
    try:
        with use_execution_authority(OWNER), pytest.raises(APIException) as raised:
            if mode == "disconnect":
                await mcp_api.disconnect_mcp_oauth(connection.connection_id)
            else:
                await mcp_api.complete_mcp_oauth(
                    connection.connection_id,
                    MCPOAuthCompleteRequest(code="code-a", state="attempt-a"),
                )
        assert raised.value.status_code == 503
        assert raised.value.error_type == "MCPMutationUnavailable"
        assert raised.value.details == {"committed": True, "retry_safe": False}
        tokens = await TokenStorageAdapter(
            async_key_value=service.oauth_storage(OWNER, connection.connection_id),
            server_url=connection.require_url(),
        ).get_tokens()
        assert (tokens.access_token if tokens is not None else None) == (
            None if mode == "disconnect" else "saved-oauth-token"
        )
        assert records[0]["committed"] is True
        assert records[0]["phase"] == "runtime_invalidation"
        assert "saved-oauth-token" not in repr(records)
        assert "sensitive runtime fixture error" not in str(raised.value.detail) + repr(
            records
        )
    finally:
        await coordinator.shutdown()
        await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_drains_attempt_removed_by_superseding_start(
    tmp_path: Path,
) -> None:
    service = MCPConnectionService(
        system_root=str(tmp_path),
        secrets=EncryptedSecretsService(
            system_root=str(tmp_path),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        ),
    )
    connection = service.create_connection_for_authority(
        OWNER,
        MCPConnectionCreate(
            display_name="Drain race",
            url="https://mcp.example.test/",
            auth_mode=MCPAuthMode.OAUTH,
        ),
    )
    cancelled, release = asyncio.Event(), asyncio.Event()

    class _Coordinator(MCPOAuthCoordinator):
        async def _run_attempt(self, attempt: _Attempt) -> None:
            attempt.authorization_url.set_result("https://identity.example/?state=a")
            try:
                await attempt.callback
            finally:
                cancelled.set()
                await release.wait()

    manager = MCPConnectionManager(connections=service)
    coordinator = _Coordinator(connections=service, manager=manager)
    await coordinator.start(
        authority=OWNER,
        connection_id=connection.connection_id,
        redirect_uri="http://localhost/callback",
    )
    starting = asyncio.create_task(
        coordinator.start(
            authority=OWNER,
            connection_id=connection.connection_id,
            redirect_uri="http://localhost/callback",
        )
    )
    try:
        async with asyncio.timeout(2):
            await cancelled.wait()
        closing = asyncio.create_task(coordinator.shutdown())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await closing
        with pytest.raises(MCPOAuthError, match="closed"):
            await starting
        assert not coordinator._attempt_tasks
        assert not coordinator._attempts
    finally:
        release.set()
        await asyncio.gather(starting, return_exceptions=True)
        await coordinator.shutdown()
        await manager.shutdown()
