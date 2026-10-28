"""Provider rejection details remain useful without exposing OAuth credentials."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.openai_oauth_http import oauth_provider

pytestmark = pytest.mark.scenario


async def test_openai_oauth_exchange_and_refresh_rejection_are_sanitized(
    app: Application,
) -> None:
    for secret in (
        "PRIVATE_CODE",
        "PRIVATE_ACCESS",
        "SUCCESS_CODE",
        "PRIVATE_SAVED_ACCESS",
        "PRIVATE_REFRESH",
        "PRIVATE_RESPONSE_ACCESS",
    ):
        app.evidence.register_secret(secret)
    app.vault("OpenAIOAuthErrorSanitizationVault")
    await app.start()
    from core.llm.openai_oauth import (
        OpenAIOAuthHTTPTokenAdapter,
        set_openai_oauth_token_adapter,
    )

    with oauth_provider() as (token_url, requests):
        set_openai_oauth_token_adapter(OpenAIOAuthHTTPTokenAdapter(token_url=token_url))
        try:
            enabled = app.request(
                "/api/system/settings/general/openai_oauth_enabled",
                "PUT",
                {"value": "true"},
            )
            assert enabled.status_code == 200
            started = app.controller.get_api_test_client().post(
                "/api/system/providers/openai/oauth/start", json={}
            )
            assert started.status_code == 200
            state = started.json()["state"]
            app.evidence.register_secret(state)
            rejected = await app.request_async(
                "/api/system/providers/openai/oauth/complete",
                "POST",
                {"state": state, "code": "PRIVATE_CODE"},
            )
            assert len(requests) == 1 and requests[0]["code"] == ["PRIVATE_CODE"]
            assert 400 <= rejected.status_code < 600
            assert "Authorization expired" in rejected.text
            assert (
                "PRIVATE_CODE" not in rejected.text
                and "PRIVATE_ACCESS" not in rejected.text
            )
            status = app.request("/api/system/providers/openai/oauth/status")
            assert (
                status.status_code == 200 and status.json()["oauth_account_id"] is None
            )
            assert (
                "PRIVATE_CODE" not in status.text
                and "PRIVATE_ACCESS" not in status.text
            )
            started = app.controller.get_api_test_client().post(
                "/api/system/providers/openai/oauth/start", json={}
            )
            assert started.status_code == 200
            state = started.json()["state"]
            app.evidence.register_secret(state)
            connected = await app.request_async(
                "/api/system/providers/openai/oauth/complete",
                "POST",
                {"state": state, "code": "SUCCESS_CODE"},
            )
            assert (
                connected.status_code == 200
                and connected.json()["oauth_account_id"] == "validation-account"
            )
            assert (
                "PRIVATE_REFRESH" not in connected.text
                and "PRIVATE_SAVED_ACCESS" not in connected.text
            )
            allowed = app.request(
                "/api/system/settings/general/editable_builtin_providers",
                "PUT",
                {"value": '["openai"]'},
            )
            assert allowed.status_code == 200
            configured = app.request(
                "/api/system/providers/openai",
                "PUT",
                {"auth_mode": "oauth", "oauth_api_key_fallback_enabled": False},
            )
            assert configured.status_code == 200
            result = await chat_turn(
                app,
                {
                    "vault_name": "OpenAIOAuthErrorSanitizationVault",
                    "prompt": "Check the provider.",
                    "session_id": "oauth_refresh_rejection",
                    "tools": [],
                    "model": "gpt-mini",
                },
            )
            assert result["terminal_event"].get("event") == "error"
            assert any(
                request.get("grant_type") == ["refresh_token"] for request in requests
            )
            status = app.request("/api/system/providers/openai/oauth/status")
            assert status.status_code == 200
            assert "Refresh expired" in status.json()["oauth_last_refresh_error"]
            for secret in (
                "PRIVATE_REFRESH",
                "PRIVATE_SAVED_ACCESS",
                "PRIVATE_RESPONSE_ACCESS",
            ):
                assert secret not in status.text and secret not in str(
                    result["terminal_event"]
                )
        finally:
            set_openai_oauth_token_adapter(None)
