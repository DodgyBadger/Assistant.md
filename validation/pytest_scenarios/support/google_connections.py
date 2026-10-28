"""Controlled grants and database failures for real Google connection journeys."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterator

    from validation.pytest_scenarios.application import Application


def grant(app: Application, prefix: str, *, gmail: bool = True) -> Any:
    from core.integrations.google import (
        GMAIL_READONLY_SCOPE,
        GOOGLE_IDENTITY_SCOPES,
        GoogleOAuthTokenState,
    )

    for suffix in ("access-token", "refresh-token"):
        app.evidence.register_secret(f"{prefix}-{suffix}")
    return GoogleOAuthTokenState(
        access_token=f"{prefix}-access-token",
        refresh_token=f"{prefix}-refresh-token",
        expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        scopes=(
            (*GOOGLE_IDENTITY_SCOPES, GMAIL_READONLY_SCOPE)
            if gmail
            else GOOGLE_IDENTITY_SCOPES
        ),
        account_id=f"{prefix}-account-id",
        account_email=f"{prefix}@example.com",
    )


@contextmanager
def reject_secret_deletion(app: Application) -> Iterator[None]:
    database = app.root / "system/access.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER reject_google_secret_delete BEFORE DELETE ON encrypted_secrets BEGIN SELECT RAISE(ABORT, 'controlled cleanup constraint'); END"
        )
    try:
        yield
    finally:
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER reject_google_secret_delete")


def start_oauth(app: Application, connection_id: str) -> dict[str, Any]:
    response = app.controller.get_api_test_client().post(
        f"/api/system/connections/google/connections/{connection_id}/oauth/start"
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    query = parse_qs(urlsplit(payload["authorization_url"]).query)
    for state in query.get("state", []):
        app.evidence.register_secret(state)
    app.evidence.append("google-oauth-start.jsonl", payload)
    return payload
