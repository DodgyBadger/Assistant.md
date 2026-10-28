"""Real deferred review creation and submission through chat and review APIs."""

from __future__ import annotations

import json
from typing import Any

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_tasks import chat_turn
from validation.pytest_scenarios.support.code_tool_chat import CodeToolProvider


async def request_review(
    app: Application,
    server: CodeToolProvider,
    vault_name: str,
    session_id: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    server.tool_name = "file_write"
    server.arguments = arguments
    result = await chat_turn(
        app,
        {
            "vault_name": vault_name,
            "session_id": session_id,
            "prompt": "Propose this file operation for approval.",
            "model": "code-tool-model",
            "chat_mode": "inline_edit",
        },
    )
    assert result["terminal_event"]["event"] == "done"
    reviews = [
        event for event in result["events"] if event.get("event") == "review_required"
    ]
    assert len(reviews) == 1
    reference = reviews[0]["artifact_ref"]
    response = app.request(
        f"/api/vaults/{vault_name}/chat/{session_id}/deferred-reviews/{reference}"
    )
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["status"] == "pending"
    assert len(review["approvals"]) == 1
    assert review["approvals"][0]["tool_name"] == "file_write"
    # The API preserves provider-native ToolCallPart.args, which the real
    # OpenAI transport supplies as a JSON string rather than a Python mapping.
    recorded_args = review["approvals"][0]["args"]
    if isinstance(recorded_args, str):
        recorded_args = json.loads(recorded_args)
    assert recorded_args == arguments
    return review


async def submit_review(
    app: Application, review: dict[str, Any], overrides: dict[str, Any]
):
    reference = review["artifact_ref"]
    return await app.request_async(
        f"/api/vaults/{review['vault_name']}/chat/{review['session_id']}/deferred-reviews/{reference}/submit",
        method="POST",
        data={
            "decisions": [
                {
                    "tool_call_id": review["approvals"][0]["tool_call_id"],
                    "decision": "approve",
                    "override_args": overrides,
                }
            ]
        },
    )


def session_tool_results(vault_name: str, session_id: str):
    from core.runtime.state import get_runtime_context

    return [
        event
        for event in get_runtime_context().chat_store.get_tool_events(
            session_id, vault_name
        )
        if event.tool_name == "file_write" and event.event_type == "result"
    ]
