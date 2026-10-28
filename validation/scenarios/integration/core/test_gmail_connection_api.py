"""Persisted Gmail capability policy controls real OAuth starts and API readiness."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.google_connections import (
    grant,
    start_oauth,
)

pytestmark = pytest.mark.scenario


async def test_persisted_gmail_policy_controls_oauth_scopes_and_draft_readiness(
    app: Application,
) -> None:
    app.evidence.register_secret("api-client-secret")
    await app.start(public_url="https://assistant.example")
    from core.identity import LOCAL_USER_AUTHORITY
    from core.integrations.google import (
        GMAIL_COMPOSE_SCOPE,
        GMAIL_READONLY_SCOPE,
        GOOGLE_IDENTITY_SCOPES,
    )
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    google = runtime.google_connection
    assert google is not None
    base = "/api/system/connections/google/connections"
    selected = []
    for name, compose in (("Read only", False), ("Drafts", True)):
        response = app.request(
            base,
            "POST",
            {
                "display_name": name,
                "client_id": f"{name.replace(' ', '').lower()}.apps.googleusercontent.com",
                "gmail": {"draft_creation_enabled": compose},
            },
        )
        assert response.status_code == 200, response.text
        connection_id = response.json()["connection_id"]
        google.set_client_secret(
            LOCAL_USER_AUTHORITY, "api-client-secret", connection_id
        )
        google.save_token_state(
            LOCAL_USER_AUTHORITY, grant(app, "api-owner"), connection_id
        )
        selected.append(connection_id)
    starts = [start_oauth(app, connection_id) for connection_id in selected]
    assert set(starts[0]["requested_scopes"]) == set(GOOGLE_IDENTITY_SCOPES) | {
        GMAIL_READONLY_SCOPE
    }
    assert set(starts[1]["requested_scopes"]) == set(starts[0]["requested_scopes"]) | {
        GMAIL_COMPOSE_SCOPE
    }
    for connection_id in selected:
        response = app.request(f"{base}/{connection_id}")
        assert response.status_code == 200
        assert response.json()["gmail_draft_available"] is False
        assert response.json()["gmail_draft_missing_scopes"] == [GMAIL_COMPOSE_SCOPE]
    activity = app.request(
        "/api/system/activity-log?limit=100&tag=google-connections"
    ).json()["entries"]
    oauth_events = [
        entry["data"]
        for entry in activity
        if (entry.get("data") or {}).get("event") == "google_oauth_started"
    ]
    assert [
        (event["event"], event["status"], event["connection_id"])
        for event in reversed(oauth_events)
    ] == [
        ("google_oauth_started", "started", connection_id) for connection_id in selected
    ]
