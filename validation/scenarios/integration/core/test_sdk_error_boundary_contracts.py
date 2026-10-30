"""Real supported SDK failures remain bounded and exclude remote private bodies."""

import json

import pytest

from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.sdk_failure_provider import PRIVATE, sdk_provider

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "provider,model,secret_name,endpoint_env",
    [
        ("anthropic", "claude-sonnet-5-5", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
        ("google", "gemini-3-pro-preview", "GOOGLE_API_KEY", "GOOGLE_GEMINI_BASE_URL"),
    ],
)
@pytest.mark.parametrize("response_kind", ["structured", "text", "list", "detail"])
async def test_supported_sdk_errors_preserve_status_without_private_payload(
    app, monkeypatch, provider, model, secret_name, endpoint_env, response_kind
):
    vault = app.vault("SDKFailureVault")
    with sdk_provider(app, provider, response_kind) as server:
        monkeypatch.setenv(endpoint_env, f"http://127.0.0.1:{server.server_port}")
        await app.start()
        response = app.request(
            "/api/system/secrets", "PUT", {"name": secret_name, "value": PRIVATE[0]}
        )
        assert response.status_code == 200, response.text
        response = app.request(
            "/api/system/models/sdk-failure",
            "PUT",
            {"provider": provider, "model_string": model, "capabilities": ["text"]},
        )
        assert response.status_code == 200, response.text
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "sdk-failure",
                "prompt": "Exercise controlled SDK rejection",
                "model": "sdk-failure",
            },
        )
        assert result["terminal_event"]["event"] == "error"
        details = result["terminal_event"]["details"]
        assert details["http_status"] == 403
        assert server.requests
        activity = (app.root / "system/activity.log").read_text()
        failures = [
            json.loads(line)["data"]
            for line in activity.splitlines()
            if json.loads(line).get("data", {}).get("event") == "chat_turn_failed"
        ]
        assert len(failures) == 1
        failure = failures[0]
        assert failure["http_status"] == 403
        if response_kind == "structured":
            assert "Safe SDK rejection" in failure["provider_error_message"]
            assert len(failure["provider_error_message"]) <= 500
            assert "\n" not in failure["provider_error_message"]
            assert failure["provider_error_type"] == "permission_error"
            assert failure["provider_error_code"] == "403"
        else:
            assert "provider_error_message" not in failure
        session = app.request(
            "/api/chat/sessions/sdk-failure", params={"vault_name": vault.name}
        )
        exported = json.dumps(result["terminal_event"]) + session.text + activity
        for private in PRIVATE:
            assert private not in exported
