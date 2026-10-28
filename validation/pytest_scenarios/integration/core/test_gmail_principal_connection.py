"""Google credentials, encrypted grants and Gmail selectors remain principal scoped."""

from __future__ import annotations

import sqlite3

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.google_connections import (
    grant,
    reject_secret_deletion,
)

pytestmark = pytest.mark.scenario


async def test_google_grants_are_owner_scoped_encrypted_and_invalidated_atomically(
    app: Application,
) -> None:
    for value in (
        "owner-client-secret",
        "work-client-secret",
        "rotated-client-secret",
        "replacement-client-secret",
    ):
        app.evidence.register_secret(value)
    await app.start()
    from core.connections import (
        ConnectionRequirement,
        GoogleConnectionCreate,
        GoogleConnectionUpdate,
        connection_requirement_available,
    )
    from core.identity import ExecutionAuthority, use_execution_authority
    from core.integrations.google import (
        GMAIL_COMPOSE_SCOPE,
        GMAIL_READONLY_SCOPE,
        GoogleCapability,
    )
    from core.runtime.state import get_runtime_context
    from core.settings.store import get_enabled_tools_config

    runtime = get_runtime_context()
    connections = runtime.built_in_connections
    google = runtime.google_connection
    assert google is not None
    owner, other = ExecutionAuthority("google-owner"), ExecutionAuthority(
        "google-other"
    )
    assert google.status(owner).state == "not_configured"
    connections.create_google_connection_for_authority(
        owner,
        GoogleConnectionCreate(
            display_name="Google", client_id="owner.apps.googleusercontent.com"
        ),
    )
    assert google.status(owner).state == "not_configured"
    google.set_client_secret(owner, "owner-client-secret")
    assert google.status(owner).state == "authorization_required"
    google.save_token_state(owner, grant(app, "partial", gmail=False))
    read = google.capability_availability(owner, GoogleCapability.GMAIL_READ)
    compose = google.capability_availability(owner, GoogleCapability.GMAIL_COMPOSE)
    assert not read.available and read.missing_scopes == (GMAIL_READONLY_SCOPE,)
    assert not compose.available and GMAIL_COMPOSE_SCOPE in compose.missing_scopes
    google.save_token_state(owner, grant(app, "owner"))
    status = google.status(owner)
    assert (
        status.state,
        status.connected,
        status.account_email,
        status.client_secret_present,
        google.capability_availability(owner, GoogleCapability.GMAIL_READ).available,
    ) == ("ready", True, "owner@example.com", True, True)
    compose = google.capability_availability(owner, GoogleCapability.GMAIL_COMPOSE)
    assert not compose.available and compose.missing_scopes == (GMAIL_COMPOSE_SCOPE,)
    with use_execution_authority(owner):
        assert connection_requirement_available(ConnectionRequirement.GOOGLE_GMAIL_READ)
        assert "gmail" in get_enabled_tools_config()
    assert google.status(other).state == "not_configured"
    work = connections.create_google_connection_for_authority(
        owner,
        GoogleConnectionCreate(
            display_name="Work Gmail", client_id="work.apps.googleusercontent.com"
        ),
    )
    google.set_client_secret(owner, "work-client-secret", work.connection_id)
    google.save_token_state(owner, grant(app, "work"), work.connection_id)
    assert runtime.gmail is not None
    assert [
        (item["connection"], item["account_email"])
        for item in runtime.gmail.list_connections(owner)
    ] == [("google", "owner@example.com"), ("work-gmail", "work@example.com")]
    assert runtime.gmail.status(owner)["account_email"] == "owner@example.com"
    assert (
        runtime.gmail.status(owner, "work-gmail")["account_email"] == "work@example.com"
    )
    database = (app.root / "system/access.db").read_bytes()
    for sensitive in (
        b"owner-client-secret",
        b"owner-access-token",
        b"owner-refresh-token",
        b"owner@example.com",
        b"owner-account-id",
        b"work-client-secret",
        b"work-access-token",
    ):
        assert sensitive not in database
    assert b"owner.apps.googleusercontent.com" in database
    previous = (google.resolve_client_credential(owner), google.load_token_state(owner))
    # An actual SQLite abort exercises the production cleanup transaction.
    with reject_secret_deletion(app), pytest.raises(sqlite3.IntegrityError):
        google.set_client_secret(owner, "rotated-client-secret")
    assert (
        google.resolve_client_credential(owner),
        google.load_token_state(owner),
    ) == previous
    google.set_client_secret(owner, "rotated-client-secret")
    assert google.status(owner).state == "authorization_required"
    original = connections.get_google_connection_for_authority(owner)
    assert original is not None
    changed = google.update_connection(
        owner,
        original.connection_id,
        GoogleConnectionUpdate(
            client_id="changed.apps.googleusercontent.com",
            display_name=original.display_name,
            gmail=original.gmail,
        ),
    )
    assert (
        changed.oauth_generation,
        google.status(owner).state,
        google.capability_availability(owner, GoogleCapability.GMAIL_READ).available,
    ) == (original.oauth_generation + 1, "not_configured", False)
    restored = google.update_connection(
        owner,
        original.connection_id,
        GoogleConnectionUpdate(
            client_id=original.client_id,
            display_name=original.display_name,
            gmail=original.gmail,
        ),
    )
    assert (restored.oauth_generation, google.status(owner).state) == (
        original.oauth_generation + 2,
        "not_configured",
    )
    google.set_client_secret(owner, "replacement-client-secret")
    assert google.status(owner).state == "authorization_required"
    google.disconnect(owner)
    assert google.status(owner).state == "not_configured"
