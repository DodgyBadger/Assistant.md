"""Actual HTTP status, body bounds, MIME faults and network retries preserve Gmail categories."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.gmail_http import (
    configure_gmail,
    corrupt_google_grant,
    gmail_server,
    http_factory,
    message_payload,
)

pytestmark = pytest.mark.scenario


async def test_gmail_draft_dispatch_outcomes_and_pre_request_authorization(
    app: Application,
) -> None:
    await app.start()
    from core.integrations.google.connection import GoogleConnectionConfigurationError
    from core.integrations.google.gmail import GmailError
    from core.integrations.google.oauth import GoogleOAuthError

    with gmail_server(app) as provider:
        authority, connection, _, missing = configure_gmail(
            app, provider, save_grant=False
        )
        with pytest.raises(GoogleOAuthError):
            await missing.create_draft(subject="Not dispatched", body="Body")
        assert provider.requests == []
        authority, connection, _, client = configure_gmail(app, provider, name="Ready")
        cases = [
            ({"status": 503}, "mutation_outcome_unknown"),
            ({"status": 408}, "mutation_outcome_unknown"),
            (
                {"status": 400, "payload": {"error": {"message": "invalid"}}},
                "validation",
            ),
            ({"body": b"not-json"}, "mutation_outcome_unknown"),
            ({"body": b"x" * (64 * 1024 + 1)}, "mutation_outcome_unknown"),
            (
                {"payload": {"message": {"id": "message-only"}}},
                "mutation_outcome_unknown",
            ),
        ]
        for response, category in cases:
            before = len(provider.requests)
            provider.queue(**response)
            with pytest.raises(GmailError) as raised:
                await client.create_draft(subject="Created?", body="Body")
            assert (
                len(provider.requests) - before,
                raised.value.category,
                raised.value.retryable,
            ) == (1, category, False)
        corrupt_google_grant(app, connection, authority)
        before = len(provider.requests)
        with pytest.raises(GoogleConnectionConfigurationError):
            await client.create_draft(subject="Not dispatched", body="Body")
        assert len(provider.requests) == before


async def test_gmail_attachment_bounds_decode_and_retry_real_http(
    app: Application,
) -> None:
    await app.start()
    from core.integrations.google.gmail import GmailError

    with gmail_server(app) as provider:
        _, _, _, client = configure_gmail(app, provider)
        assert (
            await client.download_attachment("message-1", "attachment-1", max_bytes=32)
            == b"%PDF-1.7\nexample"
        )
        with pytest.raises(GmailError) as raised:
            await client.download_attachment("message-1", "attachment-1", max_bytes=4)
        assert raised.value.category == "attachment_too_large"
        for response, category in [
            ({"payload": {"size": 4, "data": "!invalid!"}}, "provider_response"),
            ({"body": b"not-json"}, "provider_response"),
            (
                {"body": b"{}", "headers": {"Content-Length": "5000"}},
                "attachment_too_large",
            ),
            ({"body": b"x" * 5000, "chunked": True}, "attachment_too_large"),
        ]:
            provider.queue(**response)
            with pytest.raises(GmailError) as raised:
                await client.download_attachment(
                    "message-1", "attachment-1", max_bytes=32
                )
            assert raised.value.category == category
        before = len(provider.requests)
        provider.queue(status=429, headers={"Retry-After": "0"})
        provider.queue(status=503)
        provider.queue(payload={"size": 16, "data": "JVBERi0xLjcKZXhhbXBsZQ"})
        assert (
            await client.download_attachment("message-1", "attachment-1", max_bytes=32)
            == b"%PDF-1.7\nexample"
        )
        assert len(provider.requests) - before == 3


async def test_gmail_search_authentication_empty_retry_timeout_and_malformed_mime(
    app: Application,
) -> None:
    await app.start()
    from core.integrations.google.gmail import GmailAPIClient, GmailError
    from core.runtime.state import get_runtime_context

    with gmail_server(app) as provider:
        authority, connection, _, client = configure_gmail(app, provider)
        provider.queue(status=429, headers={"Retry-After": "0"})
        provider.queue(status=503)
        provider.queue(payload={"messages": [], "resultSizeEstimate": 0})
        empty = await client.search(query="label:does-not-exist", max_results=1)
        assert (len(provider.requests), empty.result_count, empty.partial) == (
            3,
            0,
            False,
        )
        provider.queue(status=401)
        with pytest.raises(GmailError) as raised:
            await client.search(query="newer_than:1d", max_results=1)
        assert (raised.value.category, raised.value.retryable) == (
            "authentication",
            False,
        )
        timed = GmailAPIClient(
            access_token_provider=lambda: get_runtime_context().google_oauth.access_token(
                authority, connection.connection_id
            ),
            http_client_factory=http_factory(provider, timeout=0.03),
            sleep=provider.retry_pause,
        )
        before = len(provider.requests)
        for _ in range(3):
            provider.queue(delay=0.15)
        with pytest.raises(GmailError) as raised:
            await timed.search(query="newer_than:1d", max_results=1)
        assert (
            len(provider.requests) - before,
            raised.value.category,
            raised.value.retryable,
        ) == (3, "network", True)
        malformed = message_payload("message-bad", "thread-bad")
        malformed["payload"]["parts"][0]["body"]["data"] = "!invalid!"
        provider.queue(payload=malformed)
        assert (await client.get_message("message-bad", max_characters=10)).text == ""
