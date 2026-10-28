"""Provider billing failures retain safe recovery metadata across chat surfaces."""

import json

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_tasks import chat_turn
from validation.pytest_scenarios.support.model_failures import (
    PRIVATE_VALUES,
    rejecting_provider,
    require_activity,
)

pytestmark = pytest.mark.scenario


async def test_provider_billing_failure_is_classified_and_sanitized(
    app: Application,
) -> None:
    vault = app.vault("ModelFailureClassificationVault")
    app.evidence.register_secret(*PRIVATE_VALUES)
    with rejecting_provider() as (server, requests):
        app.evidence.register_secret(f"http://127.0.0.1:{server.server_port}/v1")
        await app.start()
        for name, value in (
            (
                "VALIDATION_PROVIDER_URL",
                f"http://127.0.0.1:{server.server_port}/v1",
            ),
            ("VALIDATION_PROVIDER_KEY", "validation-provider-key"),
        ):
            secret = app.request(
                "/api/system/secrets",
                method="PUT",
                data={"name": name, "value": value},
            )
            assert secret.status_code == 200
        provider = app.request(
            "/api/system/providers/validation-provider",
            method="PUT",
            data={
                "base_url": "VALIDATION_PROVIDER_URL",
                "api_key": "VALIDATION_PROVIDER_KEY",
            },
        )
        assert provider.status_code == 200, provider.text
        model = app.request(
            "/api/system/models/validation-provider-model",
            method="PUT",
            data={
                "provider": "validation-provider",
                "model_string": "local-model",
                "capabilities": ["text"],
            },
        )
        assert model.status_code == 200, model.text
        session_id = "model_failure_classification_session"
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "prompt": "Trigger provider billing failure.",
                "session_id": session_id,
                "tools": [],
                "model": "validation-provider-model",
            },
        )
        assert result["start_response"].status_code == 200
        error_event = result["terminal_event"]
        assert error_event.get("event") == "error"
        assert error_event.get("details", {}).get("failure_kind") == "billing"
        assert error_event.get("details", {}).get("retryable") is False
        assert error_event.get("details", {}).get("http_status") == 400
        assert requests
        assert all(
            authorization == "Bearer validation-provider-key"
            for _, authorization in requests
        )

        detail = app.request(f"/api/chat/sessions/{session_id}?vault_name={vault.name}")
        assert detail.status_code == 200
        latest_failure = detail.json().get("latest_failure")
        assert latest_failure is not None
        assert latest_failure.get("error_type") == "ModelHTTPError"
        assert latest_failure.get("failure_kind") == "billing"
        assert latest_failure.get("retryable") is False
        assert latest_failure.get("http_status") == 400
        assert latest_failure.get("suggested_action")
        assert "credit balance" in latest_failure["error"]

        activity_log = app.request("/api/system/activity-log")
        assert activity_log.status_code == 200
        entries = activity_log.json()["entries"]
        assert any(
            entry["data"].get("failure_kind") == "billing"
            and entry["data"].get("retryable") is False
            for entry in entries
        )
        content = json.dumps(entries)
        diagnostics = [
            entry["data"]
            for entry in entries
            if entry["data"].get("event") == "chat_turn_failed"
            and entry["data"].get("session_id") == session_id
        ]
        assert diagnostics
        assert diagnostics[0]["provider_error_type"] == "invalid_request_error"
        assert diagnostics[0]["provider_error_code"] == "billing_error"
        assert len(diagnostics[0]["provider_error_message"]) <= 500
        for artifact in (json.dumps(error_event), detail.text, content):
            for private in (
                "PRIVATE_PROVIDER_INPUT",
                "validation-provider-key",
                "PRIVATE_REFRESH",
                "PRIVATE_BEARER",
                "PRIVATE_DOCUMENT",
                "user:password",
                "PRIVATE_QUERY",
            ):
                assert private not in artifact

        require_activity(
            app,
            "chat_turn_failed",
            fields={
                "session_id": session_id,
                "failure_kind": "billing",
                "retryable": False,
                "http_status": 400,
            },
        )
