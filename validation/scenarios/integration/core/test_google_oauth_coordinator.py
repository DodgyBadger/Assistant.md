"""Real Google OAuth exchanges, durable grants, and concurrent refresh fencing."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.oauth_http_inputs import (
    google_oauth_server,
    google_setup,
    start_google,
)

pytestmark = pytest.mark.scenario


async def test_google_oauth_pkce_callback_identity_and_serialized_refresh(
    app: Application,
):
    await app.start()
    from core.connections import GoogleConnectionCreate
    from core.integrations.google import GMAIL_READONLY_SCOPE, GoogleOAuthError

    with google_oauth_server(app) as server:
        owner, first, google, connections, coordinator = google_setup(app, server)
        started, query = start_google(app, coordinator, owner, first)
        assert (
            query["access_type"],
            query["include_granted_scopes"],
            query["code_challenge_method"],
        ) == (["offline"], ["true"], ["S256"])
        assert GMAIL_READONLY_SCOPE in query["scope"][0]
        with pytest.raises(GoogleOAuthError):
            await coordinator.complete(
                authority=owner, code="wrong-code", state="wrong-state"
            )
        secondary = connections.create_google_connection_for_authority(
            owner,
            GoogleConnectionCreate(
                display_name="Secondary",
                client_id="secondary.apps.googleusercontent.com",
            ),
        )
        google.set_client_secret(owner, "client-secret", secondary.connection_id)
        _, second_query = start_google(app, coordinator, owner, secondary)
        completed = await coordinator.complete(
            authority=owner, code="authorization-code", state=query["state"][0]
        )
        assert (
            completed.access_token,
            completed.refresh_token,
            completed.account_email,
            google.status(owner, first.connection_id).state,
        ) == (
            "initial-access-token",
            "initial-refresh-token",
            "owner@example.com",
            "ready",
        )
        second = await coordinator.complete(
            authority=owner, code="secondary-code", state=second_query["state"][0]
        )
        assert second.account_email == "owner@example.com"
        assert google.status(owner, secondary.connection_id).state == "ready"
        refreshed = await coordinator.refresh(owner, first.connection_id)
        assert (refreshed.access_token, refreshed.refresh_token) == (
            "refreshed-access-token",
            "initial-refresh-token",
        )
        google.save_token_state(
            owner,
            replace(
                completed,
                expires_at=(datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            ),
            first.connection_id,
        )
        before = sum(row["step"] == "refresh" for row in server.requests)
        tokens = await asyncio.gather(
            *(coordinator.access_token(owner, first.connection_id) for _ in range(3))
        )
        assert tokens == ["refreshed-access-token"] * 3
        assert sum(row["step"] == "refresh" for row in server.requests) - before == 1
        assert any(row["body"].get("code_verifier") for row in server.requests)
        with pytest.raises(GoogleOAuthError):
            await coordinator.complete(
                authority=owner, code="replayed-code", state=query["state"][0]
            )
        stored = (app.root / "system/access.db").read_bytes()
        assert (
            b"initial-access-token" not in stored
            and b"initial-refresh-token" not in stored
        )


@pytest.mark.parametrize("step", ["token", "identity", "refresh"])
@pytest.mark.parametrize("mutation", ["secret", "client-id", "disconnect"])
async def test_google_oauth_rejects_responses_crossing_connection_mutations(
    app: Application, step, mutation
):
    await app.start()
    from core.connections import GoogleConnectionUpdate
    from core.integrations.google import GoogleOAuthError

    with google_oauth_server(app) as server:
        owner, connection, google, _, coordinator = google_setup(app, server)
        _, query = start_google(app, coordinator, owner, connection)
        if step == "refresh":
            await coordinator.complete(
                authority=owner, code="seed-grant-code", state=query["state"][0]
            )
        server.block = step
        task = asyncio.create_task(
            coordinator.refresh(owner, connection.connection_id)
            if step == "refresh"
            else coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="raced-code",
                state=query["state"][0],
            )
        )
        try:
            await server.wait_entered()
            if mutation == "secret":
                google.set_client_secret(
                    owner, "replacement-secret", connection.connection_id
                )
            elif mutation == "client-id":
                google.update_connection(
                    owner,
                    connection.connection_id,
                    GoogleConnectionUpdate(
                        client_id="replacement.apps.googleusercontent.com",
                        display_name=connection.display_name,
                        gmail=connection.gmail,
                    ),
                )
            else:
                google.clear_token_state(owner, connection.connection_id)
            server.release.set()
            with pytest.raises(GoogleOAuthError):
                await task
            assert google.load_token_state(owner, connection.connection_id) is None
        finally:
            server.release.set()
            await asyncio.gather(task, return_exceptions=True)


async def test_google_oauth_refresh_preserves_newer_reauthorization(app: Application):
    await app.start()
    from core.integrations.google import GoogleOAuthError

    with google_oauth_server(app) as server:
        owner, connection, google, _, coordinator = google_setup(app, server)
        _, query = start_google(app, coordinator, owner, connection)
        initial = await coordinator.complete(
            authority=owner, code="first-code", state=query["state"][0]
        )
        server.block = "refresh"
        task = asyncio.create_task(coordinator.refresh(owner, connection.connection_id))
        try:
            await server.wait_entered()
            app.evidence.register_secret("reauthorized-access", "reauthorized-refresh")
            newer = replace(
                initial,
                access_token="reauthorized-access",
                refresh_token="reauthorized-refresh",
            )
            google.save_token_state(owner, newer, connection.connection_id)
            server.release.set()
            with pytest.raises(GoogleOAuthError):
                await task
            assert google.load_token_state(owner, connection.connection_id) == newer
        finally:
            server.release.set()
            await asyncio.gather(task, return_exceptions=True)
