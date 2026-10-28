"""Google rejects stale callback credentials before HTTP and stale grants before save."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.oauth_http_inputs import (
    google_oauth_server,
    google_setup,
    start_google,
)

pytestmark = pytest.mark.scenario


async def test_google_oauth_changed_client_rejects_callback_before_exchange(
    app: Application,
):
    await app.start()
    from core.connections import GoogleConnectionUpdate
    from core.integrations.google import GoogleOAuthError

    with google_oauth_server(app) as server:
        owner, connection, google, _, coordinator = google_setup(app, server)
        _, query = start_google(app, coordinator, owner, connection)
        google.update_connection(
            owner,
            connection.connection_id,
            GoogleConnectionUpdate(
                client_id="replacement.apps.googleusercontent.com",
                display_name=connection.display_name,
                gmail=connection.gmail,
            ),
        )
        with pytest.raises(GoogleOAuthError):
            await coordinator.complete(
                authority=owner,
                connection_id=connection.connection_id,
                code="stale-client-code",
                state=query["state"][0],
            )
        assert server.requests == []


async def test_google_oauth_stale_credential_cannot_recreate_cleared_grant(
    app: Application,
):
    await app.start()
    from core.connections import GoogleConnectionUpdate
    from core.integrations.google import GMAIL_READONLY_SCOPE, GoogleOAuthTokenState

    with google_oauth_server(app) as server:
        owner, connection, google, _, _ = google_setup(app, server)
        captured = google.resolve_client_credential(owner, connection.connection_id)
        assert captured is not None
        google.set_client_secret(owner, "replacement-secret", connection.connection_id)
        app.evidence.register_secret("stale-inflight-token", "stale-inflight-refresh")
        with pytest.raises(ValueError):
            google.save_token_state(
                owner,
                GoogleOAuthTokenState(
                    access_token="stale-inflight-token",
                    refresh_token="stale-inflight-refresh",
                    scopes=(GMAIL_READONLY_SCOPE,),
                    account_id="stale-account",
                    account_email="stale@example.com",
                ),
                connection.connection_id,
                expected_credential=captured,
            )
        assert google.load_token_state(owner, connection.connection_id) is None
        updated = google.update_connection(
            owner,
            connection.connection_id,
            GoogleConnectionUpdate(
                client_id="generation-b.apps.googleusercontent.com",
                display_name=connection.display_name,
                gmail=connection.gmail,
            ),
        )
        google.set_client_secret(owner, "replacement-secret", updated.connection_id)
        assert (
            google.resolve_client_secret(owner, updated.connection_id)
            == "replacement-secret"
        )
        assert server.requests == []
