"""Google connection API preserves state when invalid default changes are rejected."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_default_and_duplicate_name_errors_preserve_connections(
    app: Application,
) -> None:
    await app.start()
    base = "/api/system/connections/google/connections"
    created = []
    for name in ("First", "Second"):
        response = app.request(
            base,
            "POST",
            {
                "display_name": name,
                "client_id": f"{name.lower()}.apps.googleusercontent.com",
            },
        )
        assert response.status_code == 200, response.text
        created.append(response.json())
    first, second = created
    url = f"{base}/{second['connection_id']}"
    promoted = app.request(
        url,
        "PUT",
        {
            "client_id": second["client_id"],
            "display_name": second["display_name"],
            "is_default": True,
        },
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["is_default"] is True
    before = app.request(base).json()
    unset = app.request(
        url,
        "PUT",
        {
            "client_id": second["client_id"],
            "display_name": second["display_name"],
            "is_default": False,
        },
    )
    assert unset.status_code == 400, unset.text
    assert app.request(base).json() == before
    duplicate = app.request(
        url,
        "PUT",
        {
            "client_id": second["client_id"],
            "display_name": first["display_name"],
            "is_default": True,
        },
    )
    assert duplicate.status_code == 400, duplicate.text
    assert app.request(base).json() == before
