"""Real connection grants and Gmail HTTP draft outcomes preserve policy and safe activity."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import ToolReturn

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.gmail_http import (
    configure_gmail,
    corrupt_google_grant,
    draft_message,
    gmail_server,
    http_factory,
)

pytestmark = pytest.mark.scenario


async def test_gmail_draft_policy_scope_bounds_and_confidential_activity(
    app: Application,
) -> None:
    app.vault("GmailDraftVault")
    await app.start()
    from core.identity import use_execution_authority
    from core.integrations.google.gmail_service import GmailConfigurationError
    from core.runtime.state import get_runtime_context
    from core.tools.gmail import Gmail

    with gmail_server(app) as provider:
        app.evidence.register_secret("Draft body")
        authority, connection, service, _ = configure_gmail(app, provider)
        draft = await service.create_draft(
            authority, subject="Hello", body="Draft body", connection=connection.slug
        )
        assert draft.draft_id == "draft-1"
        assert len(provider.requests) == 1
        mime = draft_message(provider.requests[0])
        assert (mime["Subject"], mime.get_content().strip()) == ("Hello", "Draft body")
        activity = app.request(
            "/api/system/activity-log", params={"limit": 200, "tag": "gmail-resource"}
        )
        assert activity.status_code == 200
        lifecycle = [
            entry["data"]
            for entry in reversed(activity.json()["entries"])
            if entry["data"].get("event")
            in {"gmail_draft_creation_started", "gmail_draft_creation_completed"}
        ]
        assert [(row["event"], row["status"]) for row in lifecycle] == [
            ("gmail_draft_creation_started", "started"),
            ("gmail_draft_creation_completed", "completed"),
        ]
        assert "Draft body" not in repr(lifecycle)
        assert len({row["operation_id"] for row in lifecycle}) == 1
        assert all(row["operation_id"] for row in lifecycle)
        before = len(provider.requests)
        for body in ("", "x" * 21):
            with pytest.raises(ValueError):
                await service.create_draft(authority, subject="Hello", body=body)
        assert len(provider.requests) == before
        _, disabled, disabled_service, _ = configure_gmail(
            app, provider, name="Drafts Disabled", compose=False, is_default=False
        )
        with pytest.raises(GmailConfigurationError):
            await disabled_service.create_draft(
                authority, subject="Hello", body="Body", connection=disabled.slug
            )
        _, missing_scope, missing_scope_service, _ = configure_gmail(
            app, provider, name="Read Only", grant_compose=False, is_default=False
        )
        with pytest.raises(GmailConfigurationError):
            await missing_scope_service.create_draft(
                authority, subject="Hello", body="Body", connection=missing_scope.slug
            )
        assert len(provider.requests) == before
        get_runtime_context().gmail = service
        provider.queue(
            payload={
                "id": "draft-1",
                "message": {"id": "message-1", "threadId": "thread-1"},
            }
        )
        with use_execution_authority(authority):
            tool = Gmail.get_tool()
            raw = await tool.function(
                operation="create_draft",
                connection=connection.slug,
                subject="Hello",
                body="Draft body",
            )
        payload = json.loads(raw)
        assert (payload["draft_id"], payload["message_id"], payload["thread_id"]) == (
            "draft-1",
            "message-1",
            "thread-1",
        )
        assert "Draft body" not in raw
        before = len(provider.requests)
        with use_execution_authority(authority):
            for unsupported in ("update_draft", "send_draft", "delete_draft"):
                rejected = await tool.function(operation=unsupported)
                assert rejected.metadata["status"] == "failed"
        assert len(provider.requests) == before


async def test_gmail_missing_resource_and_real_configuration_network_failures_settle(
    app: Application,
) -> None:
    await app.start()
    from core.identity import use_execution_authority
    from core.integrations.google.gmail import GmailAPIClient
    from core.integrations.google.gmail_service import GmailResourceService
    from core.runtime.state import get_runtime_context
    from core.tools.gmail import Gmail

    with gmail_server(app) as provider:
        authority, connection, service, _ = configure_gmail(app, provider)
        runtime = get_runtime_context()
        with use_execution_authority(authority):
            tool = Gmail.get_tool()
            result = await tool.function(
                operation="get_message", message_id="stale-draft-message"
            )
            assert isinstance(result, ToolReturn)
            assert result.metadata == {
                "status": "failed",
                "error_type": "GmailError",
                "failure_kind": "not_found",
                "retryable": False,
                "phase": "gmail",
                "suggested_action": "Do not retry the same resource ID. Search Gmail again for a current message or thread ID. IDs returned by create_draft are not durable follow-up handles. Existing drafts cannot be edited, sent, or deleted; create another draft only when the user explicitly requests it.",
                "tool_name": "gmail",
                "operation": "get_message",
                "gmail_category": "not_found",
            }
            assert "stale-draft-message" not in str(result.return_value)
            assert "Do not retry the same resource ID" in str(
                result.return_value
            ) and "Search Gmail again" in str(result.return_value)
            for _ in range(3):
                provider.queue(delay=0.15)

            def network_client(caller):
                return GmailAPIClient(
                    access_token_provider=lambda: runtime.google_oauth.access_token(
                        caller, connection.connection_id
                    ),
                    http_client_factory=http_factory(provider, timeout=0.03),
                    sleep=provider.retry_pause,
                )

            runtime.gmail = GmailResourceService(
                connections=runtime.built_in_connections,
                google=runtime.google_connection,
                oauth=runtime.google_oauth,
                client_factory=network_client,
            )
            failed = await tool.function(
                operation="get_message", message_id="message-1"
            )
            assert isinstance(failed, ToolReturn)
            assert (failed.metadata["failure_kind"], failed.metadata["retryable"]) == (
                "transient_network",
                True,
            )
            runtime.gmail = service
            corrupt_google_grant(app, connection, authority)
            corrupt = await tool.function(
                operation="get_message", message_id="message-1"
            )
            assert isinstance(corrupt, ToolReturn)
            assert (
                corrupt.metadata["failure_kind"],
                corrupt.metadata["retryable"],
            ) == ("configuration", False)


async def test_gmail_locked_storage_returns_actionable_configuration_result(
    app: Application,
) -> None:
    await app.start()
    from validation.pytest_scenarios.support.gmail_locked import exercise_locked_gmail

    exercise_locked_gmail(app)
