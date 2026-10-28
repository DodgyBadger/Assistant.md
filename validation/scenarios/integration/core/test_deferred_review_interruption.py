"""Deferred-review interruption closes pending calls without corrupting history."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_terminal_review_closes_only_matching_pending_calls(
    app: Application,
) -> None:
    vault = app.vault("DeferredReviewInterruptionVault")
    await app.start()
    from pydantic_ai import DeferredToolRequests, DeferredToolResults
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    from core.chat.chat_store import ChatStore
    from core.chat.deferred_reviews import (
        DeferredReviewError,
        create_deferred_review,
        get_deferred_review,
        has_pending_deferred_review,
        mark_deferred_review_submitted,
        mark_deferred_review_terminal,
    )
    from core.chat.tool_history import analyze_tool_history
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        store = ChatStore()
        for case in ("partial", "portable", "unrelated", "append_failure"):
            session = f"review-settlement-{case}"
            store.ensure_session(session, vault.name, owner_principal_id="local-user")
            calls = [
                ToolCallPart("reviewed_tool", {}, tool_call_id="settled"),
                ToolCallPart("reviewed_tool", {}, tool_call_id="pending"),
            ]
            history = [
                ModelRequest(parts=[UserPromptPart("review these")]),
                ModelResponse(parts=calls),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            "reviewed_tool", "original result", tool_call_id="settled"
                        )
                    ]
                ),
            ]
            store.add_messages(session, vault.name, history)
            approvals = (
                calls[:1]
                if case == "unrelated"
                else (
                    [calls[0], replace(calls[1], tool_call_id="pending|provider-item")]
                    if case == "portable"
                    else calls
                )
            )
            review = create_deferred_review(
                vault_name=vault.name,
                session_id=session,
                originating_task_id="fixture-origin",
                requests=DeferredToolRequests(approvals=approvals),
                resume_messages=history,
                resume_config={},
            )
            mark_deferred_review_submitted(
                vault_name=vault.name,
                session_id=session,
                artifact_ref=review.artifact_ref,
                results=DeferredToolResults(
                    approvals={call.tool_call_id: True for call in calls}
                ),
                resumed_task_id="fixture-resume",
            )
            assert has_pending_deferred_review(
                vault_name=vault.name, session_id=session
            )
            if case == "unrelated":
                with pytest.raises(DeferredReviewError) as failure:
                    mark_deferred_review_terminal(
                        vault_name=vault.name,
                        session_id=session,
                        artifact_ref=review.artifact_ref,
                        status="cancelled",
                    )
                assert failure.value.code == "DeferredReviewHistoryConflict"
                assert (
                    get_deferred_review(
                        vault_name=vault.name,
                        session_id=session,
                        artifact_ref=review.artifact_ref,
                    ).status
                    == "resuming"
                )
                assert store.get_history(session, vault.name) == history
                continue
            if case == "append_failure":
                database = app.root / "system/chat_sessions.db"
                with sqlite3.connect(database) as conn:
                    conn.execute(
                        "CREATE TRIGGER reject_review_closure BEFORE INSERT ON chat_messages WHEN NEW.session_id = 'review-settlement-append_failure' BEGIN SELECT RAISE(ABORT, 'controlled closure constraint'); END"
                    )
                try:
                    with pytest.raises(sqlite3.IntegrityError):
                        mark_deferred_review_terminal(
                            vault_name=vault.name,
                            session_id=session,
                            artifact_ref=review.artifact_ref,
                            status="cancelled",
                        )
                finally:
                    with sqlite3.connect(database) as conn:
                        conn.execute("DROP TRIGGER reject_review_closure")
                assert (
                    get_deferred_review(
                        vault_name=vault.name,
                        session_id=session,
                        artifact_ref=review.artifact_ref,
                    ).status
                    == "resuming"
                )
                assert store.get_history(session, vault.name) == history
            mark_deferred_review_terminal(
                vault_name=vault.name,
                session_id=session,
                artifact_ref=review.artifact_ref,
                status="cancelled",
            )
            repaired = store.get_history(session, vault.name) or []
            assert analyze_tool_history(repaired).ok and repaired[:-1] == history
            assert (
                repaired[-1].parts[0].tool_call_id == "pending"
                and repaired[-1].parts[0].outcome == "interrupted"
            )
            with pytest.raises(DeferredReviewError) as failure:
                mark_deferred_review_terminal(
                    vault_name=vault.name,
                    session_id=session,
                    artifact_ref=review.artifact_ref,
                    status="cancelled",
                )
            assert failure.value.code == "DeferredReviewStateConflict"
            assert store.get_history(session, vault.name) == repaired


async def test_failed_resume_rolls_back_approved_write_and_settles_review(
    app: Application,
) -> None:
    from validation.scenarios.support.code_tool_chat import (
        code_tool_provider,
        configure_provider,
    )
    from validation.scenarios.support.inline_edit_reviews import (
        request_review,
        submit_review,
    )

    vault = app.vault("DeferredReviewFailureVault")
    with code_tool_provider() as server:
        await app.start()
        from core.chat.deferred_reviews import get_deferred_review
        from core.chat.tool_history import analyze_tool_history
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        session = "review-failed-resume"
        review = await request_review(
            app,
            server,
            vault.name,
            session,
            {"operation": "write", "path": "failed.md", "content": "approved write"},
        )
        assert not (vault / "failed.md").exists()
        server.fail_after_tool = True
        server.rollback_path = vault / "failed.md"
        submitted = await submit_review(app, review, {})
        assert submitted.status_code == 200
        task_id = submitted.json()["task"]["task_id"]
        task = await app.wait_task(task_id)
        assert task["status"] == "failed"
        assert server.observed_mutation == "approved write"
        assert not (vault / "failed.md").exists()
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            saved = get_deferred_review(
                vault_name=vault.name,
                session_id=session,
                artifact_ref=review["artifact_ref"],
            )
            assert (
                saved is not None
                and saved.status == "failed"
                and saved.resumed_task_id == task_id
            )
            history = (
                get_runtime_context().chat_store.get_history(session, vault.name) or []
            )
            assert analyze_tool_history(history).ok
        activity = app.request("/api/system/activity-log?limit=200").json()["entries"]
        rows = {
            entry["data"]["event"]: entry["data"]
            for entry in activity
            if entry.get("data", {}).get("artifact_ref") == review["artifact_ref"]
            and entry["data"].get("event", "").startswith("chat_deferred_review_")
        }
        assert rows["chat_deferred_review_created"]["status"] == "pending"
        assert rows["chat_deferred_review_claimed"]["status"] == "resuming"
        assert rows["chat_deferred_review_terminal"]["status"] == "failed"
        assert rows["chat_deferred_review_terminal"]["resumed_task_id"] == task_id
        assert rows["chat_deferred_review_terminal"]["reason"] == "review_resume_failed"


async def test_review_creation_failure_rolls_back_messages_and_failure_marker(
    app: Application,
) -> None:
    vault = app.vault("DeferredReviewAtomicVault")
    await app.start()
    from pydantic_ai import DeferredToolRequests
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        UserPromptPart,
    )

    from core.chat.chat_store import ChatStore
    from core.chat.deferred_reviews import (
        create_deferred_review,
        has_pending_deferred_review,
    )
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        store = ChatStore()
        session = "deferred-atomicity-session"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        store.update_session_metadata(
            session_id=session,
            vault_name=vault.name,
            metadata_update={"latest_turn_failure": {"status": "failed"}},
        )
        call = ToolCallPart(
            "file_write",
            {"operation": "write", "path": "Draft.md", "content": "draft"},
            tool_call_id="atomic-call",
        )
        history = [
            ModelRequest(parts=[UserPromptPart("write draft")]),
            ModelResponse(parts=[call]),
        ]
        database = app.root / "system/chat_sessions.db"
        with sqlite3.connect(database) as conn:
            conn.execute(
                "CREATE TRIGGER reject_review_insert BEFORE INSERT ON chat_deferred_reviews WHEN NEW.session_id = 'deferred-atomicity-session' BEGIN SELECT RAISE(ABORT, 'controlled review constraint'); END"
            )
        try:
            with pytest.raises(sqlite3.IntegrityError):
                with store.transaction() as connection:
                    store.add_messages(
                        session, vault.name, history, connection=connection
                    )
                    store.update_session_metadata(
                        session_id=session,
                        vault_name=vault.name,
                        remove_keys=("latest_turn_failure",),
                        connection=connection,
                    )
                    create_deferred_review(
                        vault_name=vault.name,
                        session_id=session,
                        originating_task_id="atomicity-probe",
                        requests=DeferredToolRequests(approvals=[call]),
                        resume_messages=history,
                        resume_config={},
                        connection=connection,
                        log_created=False,
                    )
        finally:
            with sqlite3.connect(database) as conn:
                conn.execute("DROP TRIGGER reject_review_insert")
        assert store.get_history(session, vault.name) is None
        assert store.get_session_metadata(session, vault.name)[
            "latest_turn_failure"
        ] == {"status": "failed"}
        assert not has_pending_deferred_review(
            vault_name=vault.name, session_id=session
        )
