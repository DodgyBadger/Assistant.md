"""Committed OAuth changes survive an actual stopped runtime manager."""

import json

import pytest
from fastmcp.client.auth.oauth import TokenStorageAdapter

from validation.scenarios.support.mcp_oauth_provider import (
    ACCESS,
    REFRESH,
    SECRET,
    authorize,
    create_connection,
    oauth_provider,
)

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("mode", ["live", "persisted", "disconnect"])
async def test_committed_oauth_state_survives_unavailable_runtime_and_recovers_on_restart(
    app, mode
):
    await app.start(public_url="https://assistant.example.test")
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.state import get_runtime_context

    async with oauth_provider(app) as (provider, _):
        try:
            runtime, connection = create_connection(app, provider)
            base = f"/api/system/mcp/connections/{connection.connection_id}/oauth"
            started = await app.request_async(base + "/start", "POST", {})
            assert started.status_code == 200, started.text
            _, query = await authorize(app, provider, started.json()["auth_url"])
            if mode == "persisted":
                # A real restart drops the live attempt. Completion must load the
                # pending PKCE state from the actual encrypted database.
                await app.controller.stop_system()
                await app.start(public_url="https://assistant.example.test")
                runtime = get_runtime_context()
            elif mode == "disconnect":
                completed = await app.request_async(base + "/complete", "POST", query)
                assert completed.status_code == 200, completed.text

            # Stop the real manager through its public lifecycle. The API and OAuth
            # coordinator remain available to commit storage, but the manager cannot
            # acknowledge invalidation. No service, logger or method is replaced.
            await runtime.mcp_manager.shutdown()
            if mode == "disconnect":
                response = await app.request_async(base, "DELETE")
            else:
                response = await app.request_async(base + "/complete", "POST", query)
            assert response.status_code == 503, response.text
            failure = response.json()
            assert failure["error"] == "MCPMutationUnavailable"
            details = failure["details"]
            assert details["committed"] is True
            assert details["retry_safe"] is False and details["retryable"] is False

            adapter = TokenStorageAdapter(
                async_key_value=runtime.mcp_connections.oauth_storage(
                    LOCAL_USER_AUTHORITY, connection.connection_id
                ),
                server_url=connection.require_url(),
            )
            tokens = await adapter.get_tokens()
            connected = mode != "disconnect"
            assert (tokens.access_token if tokens is not None else None) == (
                ACCESS if connected else None
            )
            status = await app.request_async(base + "/status")
            assert status.status_code == 200 and status.json()["connected"] is connected
            assert len(provider.tokens) == 1

            activity = (app.root / "system/activity.log").read_text()
            entries = [
                json.loads(line).get("data", {}) for line in activity.splitlines()
            ]
            failures = [
                row
                for row in entries
                if row.get("event") == "connection_mutation_failed"
                and row.get("connection_id") == connection.connection_id
                and row.get("phase") == "runtime_invalidation"
            ]
            assert len(failures) == 1
            assert (
                failures[0]["committed"] is True
                and failures[0]["error_type"] == "RuntimeError"
            )
            assert failures[0]["mutation_kind"] == (
                "disconnect_oauth" if mode == "disconnect" else "oauth_complete"
            )
            verifier = provider.tokens[0]["code_verifier"][0]
            app.evidence.register_secret(verifier)
            for private in (ACCESS, REFRESH, SECRET, verifier, query["state"]):
                assert private not in activity + response.text
            app.evidence.save_json(
                "committed-runtime-failure.json",
                {
                    "mode": mode,
                    "response": failure,
                    "connected_after_failure": connected,
                    "notification_failure": failures[0],
                },
            )

            # The API's recovery advice is executable: a new real runtime reuses the
            # committed grant/revocation without repeating the token exchange.
            await app.controller.stop_system()
            await app.start(public_url="https://assistant.example.test")
            recovered = await app.request_async(base + "/status")
            assert (
                recovered.status_code == 200
                and recovered.json()["connected"] is connected
            )
            if connected:
                tested = await app.request_async(
                    f"/api/system/mcp/connections/{connection.connection_id}/test",
                    "POST",
                )
                assert (
                    tested.status_code == 200 and tested.json()["ready"] is True
                ), tested.text
            assert len(provider.tokens) == 1
        finally:
            # App-owned cached sessions must close before the external server.
            await app.controller.stop_system()
