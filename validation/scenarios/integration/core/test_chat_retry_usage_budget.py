"""Automatic retry shares the model request limit across attempts."""

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_retry_provider import retry_provider
from validation.scenarios.support.chat_retry_setup import prepare_tool_free_retry
from validation.scenarios.support.chat_tasks import chat_turn

pytestmark = pytest.mark.scenario


async def test_retry_does_not_reset_model_request_budget(app: Application) -> None:
    vault = app.vault("ChatRetryBudgetVault")
    with retry_provider() as server:
        await app.start()
        prepare_tool_free_retry(app, server, vault)
        server.mode = "fail_once"
        result = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": "retry-budget",
                "prompt": "Retry within the model request limit.",
                "model": "session-ops-model",
                "context_template": "full_history.md",
            },
        )
        assert result["task"]["metadata"]["tools"] == []
        assert result["terminal_event"]["event"] == "error"
        assert any(
            event["event"] == "chat_retry_scheduled" for event in result["events"]
        )
        details = result["terminal_event"]["details"]
        assert details["setting"] == "chat_model_requests_limit"
        assert details["limit_kind"] == "request_limit" and details["limit"] == 1
        assert details["error_type"] == "UsageLimitExceeded"
        assert len(server.requests) == 1
        app.evidence.save_json("retry-budget-provider-requests.json", server.requests)
