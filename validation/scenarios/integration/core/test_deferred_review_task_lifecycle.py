"""Real approval reviews persist, reject conflicts and resume via the chat API."""

from __future__ import annotations

import asyncio
import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.scenarios.support.inline_edit_reviews import request_review

pytestmark = pytest.mark.scenario


async def test_review_submission_checks_stale_targets_and_claims_once(
    app: Application,
) -> None:
    vault = app.vault("DeferredReviewTaskVault")
    app.write(vault, "Draft.md", "Original draft\n")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        session = "review-session"
        review = await request_review(
            app,
            server,
            vault.name,
            session,
            {
                "operation": "write",
                "path": "Draft.md",
                "content": "Proposed content",
                "overwrite": True,
            },
        )
        assert (vault / "Draft.md").read_text() == "Original draft\n"
        reference = review["artifact_ref"]
        call_id = review["approvals"][0]["tool_call_id"]
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200 and detail.json()["chat_mode"] == "inline_edit"
        assert detail.json()["pending_review"]["artifact_ref"] == reference
        assert (
            review["artifact_kind"] == "deferred_tool_review"
            and review["originating_task_id"]
        )
        path = f"/api/vaults/{vault.name}/chat/{session}/deferred-reviews/{reference}/submit"
        duplicate = await app.request_async(
            path,
            "POST",
            {
                "decisions": [
                    {"tool_call_id": call_id, "decision": "approve"},
                    {"tool_call_id": call_id, "decision": "deny"},
                ]
            },
        )
        assert duplicate.status_code == 400 and duplicate.json()["details"][
            "duplicate_tool_call_ids"
        ] == [call_id]
        (vault / "Draft.md").write_text("Changed while review was pending\n")
        decision = {
            "decisions": [
                {
                    "tool_call_id": call_id,
                    "decision": "approve",
                    "override_args": {"content": "Reviewed content"},
                }
            ]
        }
        stale = await app.request_async(path, "POST", decision)
        assert (
            stale.status_code == 409
            and stale.json()["error"] == "DeferredReviewTargetConflict"
        )
        (vault / "Draft.md").write_text("Original draft\n")
        responses = await asyncio.gather(
            app.request_async(path, "POST", decision),
            app.request_async(path, "POST", decision),
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        accepted = next(
            response for response in responses if response.status_code == 200
        )
        task_id = accepted.json()["task"]["task_id"]
        task = await app.wait_task(task_id)
        assert task["status"] == "completed"
        assert (vault / "Draft.md").read_text() == "Reviewed content"
        final = app.request(
            f"/api/vaults/{vault.name}/chat/{session}/deferred-reviews/{reference}"
        ).json()
        assert final["status"] == "completed" and final["resumed_task_id"] == task_id
        assert (
            app.request(
                f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
            ).json()["pending_review"]
            is None
        )
        mode = app.request(
            f"/api/chat/sessions/{session}/mode",
            "PATCH",
            {"vault_name": vault.name, "chat_mode": "normal"},
        )
        assert mode.status_code == 200 and mode.json()["chat_mode"] == "normal"
        assert (
            app.request(
                f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
            ).json()["chat_mode"]
            == "normal"
        )
        assert (await app.request_async(path, "POST", decision)).status_code == 409
        # Tool execution is evidenced by actual provider tool-return messages.
        assert any(
            message.get("role") == "tool"
            for request in server.calls
            for message in request["messages"]
        )


async def test_two_reviews_block_new_turn_and_preserve_denial(app: Application) -> None:
    from validation.scenarios.support.chat_tasks import chat_turn
    from validation.scenarios.support.two_review_provider import (
        two_review_provider,
    )

    vault = app.vault("DeferredReviewTwoCallsVault")
    app.write(vault, "Draft.md", "Original draft\n")
    app.write(vault, "Notes.md", "Original notes\n")
    with two_review_provider() as server:
        await app.start()
        configure_provider(app, server)
        session = "review-two-calls"
        initial = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Review both files.",
                "model": "code-tool-model",
                "thinking": "low",
                "chat_mode": "inline_edit",
            },
        )
        reviews = [
            event
            for event in initial["events"]
            if event.get("event") == "review_required"
        ]
        assert len(reviews) == 1 and reviews[0]["review_count"] == 2
        review = reviews[0]
        assert (
            initial["terminal_event"]["choices"][0]["finish_reason"]
            == "tool_review_required"
        )
        from pydantic_ai.messages import SystemPromptPart, ToolCallPart, UserPromptPart

        from api.services.chat_sessions import get_enabled_chat_tool_names
        from core.chat.deferred_reviews import get_deferred_review
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

        assert review["artifact_kind"] == "deferred_tool_review"
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            saved = get_deferred_review(
                vault_name=vault.name,
                session_id=session,
                artifact_ref=review["artifact_ref"],
            )
            assert saved is not None and saved.status == "pending"
            assert saved.originating_task_id == initial["task"]["task_id"]
            assert saved.review_count == 2
            resume_parts = [
                part for message in saved.resume_messages for part in message.parts
            ]
            assert any(
                isinstance(part, SystemPromptPart) and part.content.strip()
                for part in resume_parts
            )
            assert [
                part.content
                for part in resume_parts
                if isinstance(part, UserPromptPart)
            ] == ["Review both files."]
            calls = [part for part in resume_parts if isinstance(part, ToolCallPart)]
            assert [(part.tool_call_id, part.tool_name) for part in calls] == [
                ("review-call-1", "file_write"),
                ("review-call-2", "file_write"),
            ]
            call_args = [
                json.loads(part.args) if isinstance(part.args, str) else part.args
                for part in calls
            ]
            assert call_args == [
                {
                    "operation": "write",
                    "path": "Draft.md",
                    "content": "Proposed draft",
                    "overwrite": True,
                },
                {
                    "operation": "write",
                    "path": "Notes.md",
                    "content": "Proposed notes",
                    "overwrite": True,
                },
            ]
            assert saved.resume_config["model"] == "code-tool-model"
            assert saved.resume_config["tools"] == get_enabled_chat_tool_names()
            assert saved.resume_config["thinking"] == "low"
            assert saved.resume_config["chat_mode"] == "inline_edit"
        artifact = app.request(
            f"/api/vaults/{vault.name}/chat/{session}/deferred-reviews/{review['artifact_ref']}"
        )
        assert artifact.status_code == 200
        artifact_data = artifact.json()
        assert (
            artifact_data["artifact_kind"],
            artifact_data["status"],
            artifact_data["originating_task_id"],
        ) == ("deferred_tool_review", "pending", initial["task"]["task_id"])
        assert [
            approval["tool_call_id"] for approval in artifact_data["approvals"]
        ] == ["review-call-1", "review-call-2"]
        args = artifact_data["approvals"][0]["args"]
        if isinstance(args, str):
            args = json.loads(args)
        assert args["path"] == "Draft.md"
        ids = [item["tool_call_id"] for item in review["approvals"]]
        assert ids == ["review-call-1", "review-call-2"]
        assert (vault / "Draft.md").read_text() == "Original draft\n" and (
            vault / "Notes.md"
        ).read_text() == "Original notes\n"
        blocked = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Continue before approval.",
                "model": "code-tool-model",
                "chat_mode": "normal",
            },
        )
        assert blocked["terminal_event"]["event"] == "error"
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        ).json()
        assert (
            detail["chat_mode"] == "inline_edit"
            and detail["pending_review"]["artifact_ref"] == review["artifact_ref"]
        )
        path = f"/api/vaults/{vault.name}/chat/{session}/deferred-reviews/{review['artifact_ref']}/submit"
        response = await app.request_async(
            path,
            "POST",
            {
                "decisions": [
                    {
                        "tool_call_id": ids[0],
                        "decision": "approve",
                        "override_args": {"content": "Reviewed content"},
                    },
                    {"tool_call_id": ids[1], "decision": "deny", "message": ""},
                ]
            },
        )
        assert response.status_code == 200
        task = await app.wait_task(response.json()["task"]["task_id"])
        assert task["status"] == "completed"
        assert (vault / "Draft.md").read_text() == "Reviewed content"
        assert (vault / "Notes.md").read_text() == "Original notes\n"
        assert server.calls[-1]["model"] == "code-tool-model"
        returned = [
            message
            for request in server.calls
            for message in request["messages"]
            if message.get("role") == "tool"
        ]
        assert {message.get("tool_call_id") for message in returned} >= set(ids)
        denied = next(
            message for message in returned if message.get("tool_call_id") == ids[1]
        )
        assert str(denied.get("content", "")).strip()
