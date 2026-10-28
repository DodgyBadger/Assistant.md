"""A real inline review resumes through the configured fallback model."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.scenarios.support.inline_edit_reviews import (
    request_review,
    session_tool_results,
    submit_review,
)

pytestmark = pytest.mark.scenario


async def test_deferred_review_resume_uses_fallback_after_model_removal(
    app: Application,
) -> None:
    vault = app.vault("DeferredReviewAliasVault")
    with code_tool_provider() as server:
        await app.start()
        configure_provider(app, server)
        fallback = app.request(
            "/api/system/models/review-fallback",
            method="PUT",
            data={
                "provider": "code-tool-provider",
                "model_string": "review-fallback-model",
                "capabilities": ["text"],
            },
        )
        assert fallback.status_code == 200, fallback.text
        default = app.request(
            "/api/system/settings/general/default_model",
            method="PUT",
            data={"value": "review-fallback"},
        )
        assert default.status_code == 200, default.text
        review = await request_review(
            app,
            server,
            vault.name,
            "review-alias-session",
            {"operation": "write", "path": "Review.md", "content": "APPROVED_CONTENT"},
        )
        target = vault / "Review.md"
        assert not target.exists()
        assert server.calls and server.calls[0]["model"] == "code-tool-model"
        deleted = app.request("/api/system/models/code-tool-model", method="DELETE")
        assert deleted.status_code == 200, deleted.text
        assert all(
            row["name"] != "code-tool-model"
            for row in app.request("/api/system/models").json()
        )
        previous_calls = len(server.calls)
        approved = await submit_review(app, review, {})
        assert approved.status_code == 200, approved.text
        task = await app.wait_task(approved.json()["task"]["task_id"])
        assert task["status"] == "completed", task
        assert target.read_text() == "APPROVED_CONTENT"
        results = session_tool_results(vault.name, "review-alias-session")
        assert len(results) == 1
        assert json.loads(results[0].result_metadata_json)["status"] == "completed"
        assert len(server.calls) > previous_calls
        assert all(
            call["model"] == "review-fallback-model"
            for call in server.calls[previous_calls:]
        )
        app.evidence.save_json("deferred-review-provider-requests.json", server.calls)
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        assert any(
            row.get("data", {}).get("event") == "chat_model_fallback"
            and row["data"].get("session_id") == "review-alias-session"
            and row["data"].get("requested_alias") == "code-tool-model"
            and row["data"].get("effective_alias") == "review-fallback"
            for row in activity.json()["entries"]
        )
