"""Actual model requests enforce limits and persist recovery information."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.code_tool_chat import (
    call_code_tool,
    code_tool_provider,
    configure_provider,
)

pytestmark = pytest.mark.scenario


async def test_model_request_limit_reports_and_persists_recovery(
    app: Application,
) -> None:
    vault = app.vault("ChatUsageLimitsVault")
    with code_tool_provider() as server:
        await app.start()
        from pydantic_ai.messages import SystemPromptPart

        from core.chat.executor import _chat_usage_limits, _failure_recovery_message

        limits = _chat_usage_limits()
        assert limits.request_limit is None and limits.tool_calls_limit is None
        response = app.request(
            "/api/system/settings/general/chat_model_requests_limit",
            "PUT",
            {"value": "150"},
        )
        assert response.status_code == 200
        assert _chat_usage_limits().request_limit == 150
        response = app.request(
            "/api/system/settings/general/chat_model_requests_limit",
            "PUT",
            {"value": "1"},
        )
        assert response.status_code == 200
        assert _chat_usage_limits().request_limit == 1
        configure_provider(app, server)
        for session in ("chat_usage_limit_task", "chat_usage_limit_stream"):
            before = len(server.calls)
            result = await call_code_tool(
                app,
                server,
                vault,
                "allow_memory_read",
                {
                    "vault_name": vault.name,
                    "prompt": "Trigger request limit.",
                    "session_id": session,
                },
            )
            assert result["start_response"].status_code == 200
            assert len(server.calls) == before + 1
            error = result["terminal_event"]
            assert error["event"] == "error"
            assert result["text"].strip()
            assert (error["details"]["setting"], error["details"]["limit_kind"]) == (
                "chat_model_requests_limit",
                "request_limit",
            )
            detail = app.request(
                f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
            )
            assert detail.status_code == 200
            failure = detail.json()["latest_failure"]
            assert failure and failure["status"] == "failed"
            assert failure["error_type"] == "UsageLimitExceeded"
            recovery = _failure_recovery_message(failure)
            assert recovery is not None
            assert any(
                isinstance(part, SystemPromptPart) and part.content.strip()
                for part in recovery.parts
            )
