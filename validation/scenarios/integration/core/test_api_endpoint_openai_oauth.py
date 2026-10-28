"""OpenAI OAuth authorization drives actual provider and model readiness."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.openai_oauth_http import oauth_provider

pytestmark = pytest.mark.scenario


async def test_openai_oauth_manual_completion_and_readiness(app: Application) -> None:
    for secret in ("SUCCESS_CODE", "PRIVATE_SAVED_ACCESS", "PRIVATE_REFRESH"):
        app.evidence.register_secret(secret)
    app.vault("IntegrationApiVault")
    await app.start()
    from core.llm.openai_oauth import (
        OPENAI_OAUTH_CLIENT_ID,
        OPENAI_OAUTH_LOOPBACK_REDIRECT_URI,
        OPENAI_OAUTH_ORIGINATOR,
        OPENAI_OAUTH_SCOPE,
        OpenAIOAuthHTTPTokenAdapter,
        set_openai_oauth_token_adapter,
    )

    assert (
        app.request(
            "/api/system/settings/general/editable_builtin_providers",
            "PUT",
            {"value": '["openai"]'},
        ).status_code
        == 200
    )
    configured = app.request(
        "/api/system/providers/openai",
        "PUT",
        {"auth_mode": "oauth", "oauth_api_key_fallback_enabled": True},
    )
    assert configured.status_code == 200
    assert (
        configured.json()["configured_auth_mode"],
        configured.json()["effective_auth_mode"],
        configured.json()["oauth_api_key_fallback_enabled"],
    ) == ("oauth", "api_key", True)
    assert (
        app.request(
            "/api/system/settings/general/openai_oauth_enabled",
            "PUT",
            {"value": "true"},
        ).status_code
        == 200
    )
    with oauth_provider() as (token_url, requests):
        set_openai_oauth_token_adapter(OpenAIOAuthHTTPTokenAdapter(token_url=token_url))
        try:
            start = app.controller.get_api_test_client().post(
                "/api/system/providers/openai/oauth/start", json={}
            )
            assert start.status_code == 200
            payload = start.json()
            app.evidence.register_secret(payload["state"])
            assert re.fullmatch(r"[0-9a-f]{32}", payload["state"])
            assert payload["redirect_uri"] == OPENAI_OAUTH_LOOPBACK_REDIRECT_URI
            query = parse_qs(urlparse(payload["auth_url"]).query)
            assert (
                query["client_id"] == [OPENAI_OAUTH_CLIENT_ID]
                and query["scope"] == [OPENAI_OAUTH_SCOPE]
                and query["originator"] == [OPENAI_OAUTH_ORIGINATOR]
            )
            redirect = (
                f"{payload['redirect_uri']}?code=SUCCESS_CODE&state={payload['state']}"
            )
            connected = await app.request_async(
                "/api/system/providers/openai/oauth/complete",
                "POST",
                {"redirect_url": redirect},
            )
            assert connected.status_code == 200
            assert (
                connected.json()["oauth_status"],
                connected.json()["oauth_account_id"],
            ) == ("connected", "validation-account")
            assert (
                "PRIVATE_SAVED_ACCESS" not in connected.text
                and "PRIVATE_REFRESH" not in connected.text
            )
            assert len(requests) == 1 and requests[0]["code"] == ["SUCCESS_CODE"]
            models = app.request("/api/system/models")
            assert (
                models.status_code == 200
                and next(item for item in models.json() if item["name"] == "sol")[
                    "available"
                ]
                is True
            )
            assert (
                app.request("/api/system/providers/openai/oauth", "DELETE").status_code
                == 200
            )
            status = app.request("/api/system/providers/openai/oauth/status")
            assert (
                status.status_code == 200
                and status.json()["oauth_status"] == "disconnected"
            )
            models = app.request("/api/system/models")
            assert (
                models.status_code == 200
                and next(item for item in models.json() if item["name"] == "sol")[
                    "available"
                ]
                is False
            )
        finally:
            set_openai_oauth_token_adapter(None)
