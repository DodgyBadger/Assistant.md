"""Denied review calls reach the resumed provider and do not mutate files."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.chat_tasks import chat_turn
from validation.pytest_scenarios.support.code_tool_chat import configure_provider
from validation.pytest_scenarios.support.two_review_provider import two_review_provider

pytestmark = pytest.mark.scenario


async def test_two_denials_reach_resumed_model_with_optional_reason(
    app: Application,
) -> None:
    vault = app.vault("DeferredReviewDenialsVault")
    app.write(vault, "Draft.md", "Original draft\n")
    app.write(vault, "Notes.md", "Original notes\n")
    with two_review_provider() as server:
        await app.start()
        from core.constants import INLINE_EDIT_DENIAL_MESSAGE

        configure_provider(app, server)
        session = "two-denials"
        initial = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Review both changes.",
                "model": "code-tool-model",
                "chat_mode": "inline_edit",
            },
        )
        review = next(
            event
            for event in initial["events"]
            if event.get("event") == "review_required"
        )
        ids = [approval["tool_call_id"] for approval in review["approvals"]]
        assert ids == ["review-call-1", "review-call-2"]
        path = f"/api/vaults/{vault.name}/chat/{session}/deferred-reviews/{review['artifact_ref']}/submit"
        submitted = await app.request_async(
            path,
            "POST",
            {
                "decisions": [
                    {"tool_call_id": ids[0], "decision": "deny", "message": ""},
                    {
                        "tool_call_id": ids[1],
                        "decision": "deny",
                        "message": "Do not create either file.",
                    },
                ]
            },
        )
        assert submitted.status_code == 200
        task_id = submitted.json()["task"]["task_id"]
        task = await app.wait_task(task_id)
        assert task["status"] == "completed"
        assert (vault / "Draft.md").read_text() == "Original draft\n"
        assert (vault / "Notes.md").read_text() == "Original notes\n"
        resumed = server.calls[-1]
        assert resumed["model"] == "code-tool-model"
        returned = {
            message["tool_call_id"]: str(message["content"])
            for message in resumed["messages"]
            if message.get("role") == "tool"
        }
        assert INLINE_EDIT_DENIAL_MESSAGE in returned[ids[0]]
        assert "User reason:" not in returned[ids[0]]
        assert (
            f"{INLINE_EDIT_DENIAL_MESSAGE} User reason: Do not create either file."
            in returned[ids[1]]
        )
        review_response = app.request(
            f"/api/vaults/{vault.name}/chat/{session}/deferred-reviews/{review['artifact_ref']}"
        )
        assert review_response.status_code == 200
        assert (
            review_response.json()["status"],
            review_response.json()["resumed_task_id"],
        ) == ("completed", task_id)
