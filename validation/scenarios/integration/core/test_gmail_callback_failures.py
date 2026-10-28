"""Actual callback programming failures propagate unchanged before Gmail HTTP dispatch."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.gmail_callback_inputs import (
    ConfiguredGmailCallbacks,
)
from validation.scenarios.support.gmail_http import (
    configure_gmail,
    gmail_server,
    http_factory,
)

pytestmark = pytest.mark.scenario


async def test_gmail_constructor_callback_parse_failures_propagate_without_http(
    app: Application,
) -> None:
    await app.start()
    from core.identity import use_execution_authority
    from core.integrations.google.gmail import GmailAPIClient
    from core.integrations.google.gmail_service import GmailResourceService
    from core.runtime.state import get_runtime_context
    from core.tools.gmail import Gmail

    with gmail_server(app) as provider:
        authority, connection, _, _ = configure_gmail(app, provider)
        runtime = get_runtime_context()
        token_input = ConfiguredGmailCallbacks(
            runtime, authority, connection.connection_id, provider, configured_json="{"
        )
        client = GmailAPIClient(
            access_token_provider=token_input.access_token,
            http_client_factory=http_factory(provider),
        )
        with pytest.raises(json.JSONDecodeError) as client_failure:
            await client.create_draft(subject="Not dispatched", body="Body")
        assert client_failure.value is token_input.original_error
        assert str(client_failure.value) == token_input.original_message
        assert token_input.parse_count == 1
        assert provider.intended_requests == []
        assert provider.requests == []

        factory_input = ConfiguredGmailCallbacks(
            runtime, authority, connection.connection_id, provider, configured_json="{"
        )
        runtime.gmail = GmailResourceService(
            connections=runtime.built_in_connections,
            google=runtime.google_connection,
            oauth=runtime.google_oauth,
            client_factory=factory_input.client_factory,
        )
        with use_execution_authority(authority):
            with pytest.raises(json.JSONDecodeError) as tool_failure:
                await Gmail.get_tool().function(
                    operation="get_message", message_id="message-1"
                )
        assert tool_failure.value is factory_input.original_error
        assert str(tool_failure.value) == factory_input.original_message
        assert factory_input.parse_count == 1
        assert provider.intended_requests == []
        assert provider.requests == []
        app.evidence.save_json(
            "gmail-callback-failures.json",
            {
                "token_provider": {
                    "error_type": type(client_failure.value).__name__,
                    "error_message": token_input.original_message,
                    "same_exception": client_failure.value
                    is token_input.original_error,
                },
                "resource_client_factory": {
                    "error_type": type(tool_failure.value).__name__,
                    "error_message": factory_input.original_message,
                    "same_exception": tool_failure.value
                    is factory_input.original_error,
                },
                "http_requests": len(provider.requests),
            },
        )
