"""Live local provider failures retain recoverable chat classification."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.provider_stream_failures import (
    configure_provider,
    refused_loopback_port,
    stream_failure_provider,
)

pytestmark = pytest.mark.scenario


async def test_sse_overload_is_retryable_and_next_turn_recovers(
    app: Application,
) -> None:
    vault = app.vault("ProviderSSEOverloadVault")
    with stream_failure_provider() as server:
        await app.start()
        configure_provider(app, server.server_port)
        session = "provider-overload-session"
        first = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Trigger provider overload.",
                "model": "failure-model",
            },
        )
        assert first["terminal_event"]["event"] == "error"
        details = first["terminal_event"]["details"]
        assert (
            details["failure_kind"] == "provider_overloaded"
            and details["retryable"] is True
        )
        assert len(server.requests) == 1
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        failure = detail.json()["latest_failure"]
        assert (
            failure["failure_kind"] == "provider_overloaded"
            and failure["retryable"] is True
        )
        assert (
            "stream-failure-provider-key" not in detail.text
            and "stream-failure-provider-key" not in json.dumps(first["terminal_event"])
        )
        server.healthy = True
        second = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Continue after recovery.",
                "model": "failure-model",
            },
        )
        assert second["terminal_event"]["event"] == "done"
        assert second["task"]["status"] == "completed" and len(server.requests) == 2


async def test_connection_refusal_is_classified_as_retryable_network_failure(
    app: Application,
) -> None:
    vault = app.vault("ProviderConnectionRefusalVault")
    with refused_loopback_port() as port:
        await app.start()
        configure_provider(app, port)
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "provider-connection-refused",
                "prompt": "Connect to unavailable provider.",
                "model": "failure-model",
            },
        )
        assert result["terminal_event"]["event"] == "error"
        details = result["terminal_event"]["details"]
        assert (
            details["failure_kind"] == "transient_network"
            and details["retryable"] is True
        )
        detail = app.request(
            "/api/chat/sessions/provider-connection-refused",
            params={"vault_name": vault.name},
        )
        assert detail.status_code == 200
        failure = detail.json()["latest_failure"]
        assert (
            failure["failure_kind"] == "transient_network"
            and failure["retryable"] is True
        )
        assert (
            "stream-failure-provider-key" not in detail.text
            and "stream-failure-provider-key"
            not in json.dumps(result["terminal_event"])
        )
