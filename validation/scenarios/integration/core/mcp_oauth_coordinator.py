"""Deterministic validation of the headless MCP OAuth coordinator."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-mcp-oauth-flow-")
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    bootstrap_system_root = direct_root / "system"
    data_root.mkdir()
    bootstrap_system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=bootstrap_system_root)

from fastmcp.client.auth.oauth import TokenStorageAdapter  # noqa: E402
from mcp.shared.auth import OAuthToken  # noqa: E402

from core.identity import ExecutionAuthority  # noqa: E402
from core.mcp import (  # noqa: E402
    MCPAuthMode,
    MCPConnectionCreate,
    MCPConnectionManager,
    MCPConnectionService,
)
from core.mcp.oauth import (  # noqa: E402
    _PENDING_COLLECTION,
    _PENDING_KEY,
    MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS,
    MCPOAuthCoordinator,
    MCPOAuthError,
    _Attempt,
)
from core.runtime.paths import set_bootstrap_roots  # noqa: E402
from core.secrets import EncryptedSecretsService, SecretKeyring  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class _DeterministicCoordinator(MCPOAuthCoordinator):
    async def _run_attempt(self, attempt: _Attempt) -> None:
        state = "validation-state"
        storage = self._connections.oauth_storage(  # noqa: SLF001
            attempt.authority, attempt.connection.connection_id
        )
        await storage.put(
            _PENDING_KEY,
            {
                "state": state,
                "code_verifier": "validation-code-verifier",
                "redirect_uri": attempt.redirect_uri,
                "token_endpoint": "https://identity.example/token",
                "client_id": "validation-client",
                "client_secret": None,
                "token_endpoint_auth_method": "none",
                "resource": None,
                "expires_at": (
                    datetime.now(UTC)
                    + timedelta(seconds=MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS)
                ).isoformat(),
                "operation_id": attempt.operation_id,
            },
            collection=_PENDING_COLLECTION,
            ttl=MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS,
        )
        attempt.authorization_url.set_result(
            f"https://identity.example/authorize?state={state}"
        )
        code, returned_state = await attempt.callback
        if code != "validation-code" or returned_state != state:
            raise ValueError("Unexpected deterministic callback values")
        await TokenStorageAdapter(
            async_key_value=storage,
            server_url=attempt.connection.url,
        ).set_tokens(
            OAuthToken(
                access_token="validation-access-token",
                refresh_token="validation-refresh-token",
                expires_in=3600,
            )
        )


class _CompletionTimeoutCoordinator(_DeterministicCoordinator):
    async def _run_attempt(self, attempt: _Attempt) -> None:
        state = "validation-state"
        storage = self._connections.oauth_storage(  # noqa: SLF001
            attempt.authority, attempt.connection.connection_id
        )
        await storage.put(
            _PENDING_KEY,
            {
                "state": state,
                "code_verifier": "validation-code-verifier",
                "redirect_uri": attempt.redirect_uri,
                "token_endpoint": "https://identity.example/token",
                "client_id": "validation-client",
                "client_secret": None,
                "token_endpoint_auth_method": "none",
                "resource": None,
                "expires_at": (
                    datetime.now(UTC)
                    + timedelta(seconds=MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS)
                ).isoformat(),
                "operation_id": attempt.operation_id,
            },
            collection=_PENDING_COLLECTION,
            ttl=MCP_OAUTH_CALLBACK_TIMEOUT_SECONDS,
        )
        attempt.authorization_url.set_result(
            f"https://identity.example/authorize?state={state}"
        )
        await attempt.callback
        await asyncio.Event().wait()


class MCPOAuthCoordinatorScenario(BaseScenario):
    async def test_scenario(self) -> None:
        system_root = self.run_path / "system"
        system_root.mkdir()
        data_root = self.run_path / "data"
        data_root.mkdir()
        set_bootstrap_roots(data_root=data_root, system_root=system_root)
        secrets = EncryptedSecretsService(
            system_root=str(system_root),
            keyring=SecretKeyring(keys={1: bytes(range(32))}, active_version=1),
        )
        service = MCPConnectionService(system_root=str(system_root), secrets=secrets)
        owner = ExecutionAuthority("oauth-flow-owner")
        other = ExecutionAuthority("oauth-flow-other")
        connection = service.create_connection_for_authority(
            owner,
            MCPConnectionCreate(
                display_name="Mail",
                url="https://mail.example/mcp",
                auth_mode=MCPAuthMode.OAUTH,
            ),
        )
        manager = MCPConnectionManager(connections=service)
        coordinator = _DeterministicCoordinator(
            connections=service,
            manager=manager,
        )

        started = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        self.soft_assert_equal(
            (started.state, started.redirect_uri),
            ("validation-state", "https://assistant.example/api/oauth/callback"),
            "OAuth start should return the server authorization state and callback",
        )
        self.soft_assert_equal(
            (
                await coordinator.status(
                    authority=owner, connection_id=connection.connection_id
                )
            ).status,
            "pending",
            "The owner should see the active authorization attempt",
        )
        try:
            await coordinator.status(
                authority=other,
                connection_id=connection.connection_id,
            )
        except LookupError:
            pass
        else:
            self.soft_assert(False, "Another principal must not see OAuth status")

        await coordinator.shutdown()
        coordinator = MCPOAuthCoordinator(connections=service, manager=manager)
        self.soft_assert_equal(
            (
                await coordinator.status(
                    authority=owner,
                    connection_id=connection.connection_id,
                )
            ).status,
            "pending",
            "Encrypted pending state should survive coordinator restart",
        )
        with patch(
            "core.mcp.oauth.mcp_oauth_http_client_factory",
            return_value=_oauth_http_client,
        ):
            completed = await coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="validation-code",
                state="validation-state",
            )
        self.soft_assert(
            completed.connected,
            "Callback completion should persist a usable OAuth token",
        )
        self.soft_assert_equal(
            completed.operation_id,
            started.operation_id,
            "OAuth start and completion should retain one operation identity across restart",
        )
        self.soft_assert_equal(
            (
                await coordinator.status(
                    authority=owner, connection_id=connection.connection_id
                )
            ).status,
            "connected",
            "Durable token state should survive completion of the attempt",
        )
        self.soft_assert(
            b"validation-access-token" not in (system_root / "access.db").read_bytes(),
            "Completed OAuth credentials must remain encrypted at rest",
        )

        await coordinator.shutdown()
        coordinator = _DeterministicCoordinator(
            connections=service,
            manager=manager,
        )
        rejected_started = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        try:
            await coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="validation-code",
                state="wrong-state",
            )
        except MCPOAuthError:
            pass
        else:
            self.soft_assert(False, "A wrong-state live callback must be rejected")
        self.soft_assert_equal(
            (
                await coordinator.status(
                    authority=owner, connection_id=connection.connection_id
                )
            ).status,
            "pending",
            "Rejecting one callback should leave the live attempt available",
        )
        rejected_completed = await coordinator.complete(
            authority=owner,
            connection_id=connection.connection_id,
            code="validation-code",
            state="validation-state",
        )
        self.soft_assert_equal(
            rejected_completed.operation_id,
            rejected_started.operation_id,
            "A valid callback should complete after a nonterminal rejection",
        )
        rejection_records = [
            json.loads(line)
            for line in (system_root / "activity.log").read_text().splitlines()
            if line.strip()
        ]
        self.soft_assert(
            any(
                (record.get("data") or {}).get("event")
                == "mcp_oauth_completion_rejected"
                and (record.get("data") or {}).get("operation_id")
                == rejected_started.operation_id
                for record in rejection_records
            ),
            "System Activity should identify callback rejection as nonterminal",
        )

        await coordinator.shutdown()
        coordinator = _DeterministicCoordinator(
            connections=service,
            manager=manager,
        )
        failed_started = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        await coordinator.shutdown()
        coordinator = MCPOAuthCoordinator(connections=service, manager=manager)
        try:
            with patch(
                "core.mcp.oauth.mcp_oauth_http_client_factory",
                return_value=_failing_oauth_http_client,
            ):
                await coordinator.complete(
                    authority=owner,
                    connection_id=connection.connection_id,
                    code="validation-code",
                    state="validation-state",
                )
        except MCPOAuthError:
            pass
        else:
            self.soft_assert(False, "A rejected token exchange must fail completion")
        activity_text = (system_root / "activity.log").read_text()
        activity_records = [
            json.loads(line) for line in activity_text.splitlines() if line.strip()
        ]
        started_event = next(
            (
                record
                for record in activity_records
                if (record.get("data") or {}).get("event")
                == "mcp_oauth_attempt_started"
                and (record.get("data") or {}).get("operation_id")
                == failed_started.operation_id
            ),
            None,
        )
        failed_event = next(
            (
                record
                for record in activity_records
                if (record.get("data") or {}).get("event") == "mcp_oauth_attempt_failed"
                and (record.get("data") or {}).get("operation_id")
                == failed_started.operation_id
            ),
            None,
        )
        self.soft_assert(
            (started_event or {}).get("data", {}).get("status") == "started",
            "System Activity should retain the correlated OAuth attempt start",
        )
        self.soft_assert(
            (failed_event or {}).get("data", {})
            | {
                "connection_id": connection.connection_id,
                "phase": "token_exchange",
                "status": "failed",
                "error_type": "ValidationError",
            }
            == (failed_event or {}).get("data", {}),
            "System Activity should retain the correlated token-exchange failure",
        )
        failed_error = str((failed_event or {}).get("data", {}).get("error", ""))
        self.soft_assert(
            failed_error == "The MCP server rejected OAuth completion."
            and "PRIVATE_OAUTH_CREDENTIAL" not in activity_text
            and "PRIVATE_REFRESH_CREDENTIAL" not in activity_text,
            "Retained OAuth failures should use fixed text without provider credentials",
        )

        await coordinator.shutdown()
        coordinator = _CompletionTimeoutCoordinator(
            connections=service,
            manager=manager,
        )
        timeout_started = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        try:
            with patch("core.mcp.oauth.MCP_OAUTH_COMPLETE_TIMEOUT_SECONDS", 0.01):
                await coordinator.complete(
                    authority=owner,
                    connection_id=connection.connection_id,
                    code="validation-code",
                    state="validation-state",
                )
        except MCPOAuthError:
            pass
        else:
            self.soft_assert(False, "A token-exchange timeout must fail completion")
        activity_records = [
            json.loads(line)
            for line in (system_root / "activity.log").read_text().splitlines()
            if line.strip()
        ]
        timeout_event = next(
            (
                record
                for record in activity_records
                if (record.get("data") or {}).get("event") == "mcp_oauth_attempt_failed"
                and (record.get("data") or {}).get("operation_id")
                == timeout_started.operation_id
            ),
            None,
        )
        self.soft_assert(
            (timeout_event or {}).get("data", {})
            | {
                "phase": "completion",
                "status": "failed",
                "error_type": "TimeoutError",
                "error": "The MCP server did not complete OAuth authorization.",
            }
            == (timeout_event or {}).get("data", {}),
            "Completion timeouts should close the correlated OAuth lifecycle safely",
        )

        try:
            await coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="validation-code",
                state="wrong-state",
            )
        except MCPOAuthError:
            pass
        else:
            self.soft_assert(False, "A callback without an active attempt must fail")

        await coordinator.shutdown()
        coordinator = _DeterministicCoordinator(
            connections=service,
            manager=manager,
        )
        superseded_started = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        disconnected_started = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        await coordinator.disconnect(
            authority=owner,
            connection_id=connection.connection_id,
        )
        cancellation_records = [
            json.loads(line)
            for line in (system_root / "activity.log").read_text().splitlines()
            if line.strip()
        ]
        cancellation_data = [
            record.get("data") or {}
            for record in cancellation_records
            if (record.get("data") or {}).get("event") == "mcp_oauth_attempt_cancelled"
        ]
        self.soft_assert(
            any(
                data.get("operation_id") == superseded_started.operation_id
                and data.get("phase") == "superseded"
                and data.get("reason") == "superseded_by_new_attempt"
                for data in cancellation_data
            ),
            "Superseding an attempt should close its correlated activity lifecycle",
        )
        self.soft_assert(
            any(
                data.get("operation_id") == disconnected_started.operation_id
                and data.get("phase") == "disconnect"
                and data.get("reason") == "user_disconnect"
                for data in cancellation_data
            ),
            "Disconnecting should close the active correlated activity lifecycle",
        )
        self.soft_assert_equal(
            (
                await coordinator.status(
                    authority=owner, connection_id=connection.connection_id
                )
            ).status,
            "disconnected",
            "Disconnect should clear durable OAuth token state",
        )

        mutation_failure_started = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        with patch.object(
            service,
            "disconnect_oauth",
            side_effect=RuntimeError("forced durable mutation failure"),
        ):
            try:
                await coordinator.disconnect(
                    authority=owner,
                    connection_id=connection.connection_id,
                )
            except RuntimeError:
                pass
            else:
                self.soft_assert(False, "A durable disconnect failure must propagate")
        mutation_failure_records = [
            json.loads(line)
            for line in (system_root / "activity.log").read_text().splitlines()
            if line.strip()
        ]
        self.soft_assert(
            not any(
                (record.get("data") or {}).get("event") == "mcp_oauth_attempt_cancelled"
                and (record.get("data") or {}).get("operation_id")
                == mutation_failure_started.operation_id
                for record in mutation_failure_records
            ),
            "Failed durable revocation must not emit a terminal cancellation",
        )
        await coordinator.disconnect(
            authority=owner,
            connection_id=connection.connection_id,
        )
        retry_records = [
            json.loads(line)
            for line in (system_root / "activity.log").read_text().splitlines()
            if line.strip()
        ]
        retry_cancellations = [
            record.get("data") or {}
            for record in retry_records
            if (record.get("data") or {}).get("event") == "mcp_oauth_attempt_cancelled"
            and (record.get("data") or {}).get("operation_id")
            == mutation_failure_started.operation_id
        ]
        self.soft_assert_equal(
            len(retry_cancellations),
            1,
            "A successful disconnect retry should close the original operation once",
        )
        self.soft_assert(
            bool(retry_cancellations)
            and retry_cancellations[0].get("phase") == "disconnect",
            "A successful disconnect retry should preserve its cancellation phase",
        )

        supersede_failure_started = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        with patch.object(
            service,
            "disconnect_oauth",
            side_effect=RuntimeError("forced durable supersede failure"),
        ):
            try:
                await coordinator.start(
                    authority=owner,
                    connection_id=connection.connection_id,
                    redirect_uri="https://assistant.example/api/oauth/callback",
                )
            except RuntimeError:
                pass
            else:
                self.soft_assert(False, "A durable supersede failure must propagate")
        failed_supersede_records = [
            json.loads(line)
            for line in (system_root / "activity.log").read_text().splitlines()
            if line.strip()
        ]
        self.soft_assert(
            not any(
                (record.get("data") or {}).get("event") == "mcp_oauth_attempt_cancelled"
                and (record.get("data") or {}).get("operation_id")
                == supersede_failure_started.operation_id
                for record in failed_supersede_records
            ),
            "Failed durable supersede must not emit a terminal cancellation",
        )
        supersede_retry = await coordinator.start(
            authority=owner,
            connection_id=connection.connection_id,
            redirect_uri="https://assistant.example/api/oauth/callback",
        )
        supersede_retry_records = [
            json.loads(line)
            for line in (system_root / "activity.log").read_text().splitlines()
            if line.strip()
        ]
        supersede_cancellations = [
            record.get("data") or {}
            for record in supersede_retry_records
            if (record.get("data") or {}).get("event") == "mcp_oauth_attempt_cancelled"
            and (record.get("data") or {}).get("operation_id")
            == supersede_failure_started.operation_id
        ]
        self.soft_assert_equal(
            len(supersede_cancellations),
            1,
            "A successful supersede retry should close the original operation once",
        )
        self.soft_assert(
            bool(supersede_cancellations)
            and supersede_cancellations[0].get("phase") == "superseded",
            "A successful supersede retry should preserve its cancellation phase",
        )
        await coordinator.disconnect(
            authority=owner,
            connection_id=connection.connection_id,
        )
        self.soft_assert(
            supersede_retry.operation_id != supersede_failure_started.operation_id,
            "A superseding retry should create a distinct new authorization operation",
        )
        await coordinator.shutdown()
        await manager.shutdown()
        self.assert_no_failures()
        self.teardown_scenario()


def _oauth_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    del headers, timeout, auth

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://identity.example/token"
        assert b"code=validation-code" in request.content
        assert b"code_verifier=validation-code-verifier" in request.content
        return httpx.Response(
            200,
            json={
                "access_token": "validation-access-token",
                "refresh_token": "validation-refresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(respond))


def _failing_oauth_http_client(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    del headers, timeout, auth

    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": {"value": "PRIVATE_OAUTH_CREDENTIAL"},
                "refresh_token": "PRIVATE_REFRESH_CREDENTIAL",
                "token_type": "Bearer",
            },
            request=request,
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(reject))


if __name__ == "__main__":
    asyncio.run(MCPOAuthCoordinatorScenario().test_scenario())
