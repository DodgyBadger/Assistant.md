"""Terminal replay eviction reports expiry while durable conversations remain."""

import asyncio
import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn

pytestmark = pytest.mark.scenario


async def test_evicted_chat_task_is_missing_while_conversation_survives(
    app: Application,
) -> None:
    vault = app.vault("TerminalReplayRetentionVault")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    await app.start()
    tools = app.request("/api/metadata").json()["tools"]
    disabled = app.request(
        "/api/system/settings/general/disabled_tools",
        "PUT",
        {"value": json.dumps([tool["name"] for tool in tools])},
    )
    assert disabled.status_code == 200
    first = None
    for index in range(101):
        turn = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": f"retained-chat-{index}",
                "prompt": "Complete this deterministic conversation.",
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert turn["task"]["status"] == "completed"
        if first is None:
            first = turn
            available = await app.request_async(
                f"/api/chat/tasks/{first['task']['task_id']}/replay-snapshot"
            )
            assert available.status_code == 200 and available.json()["terminal"]
    assert first is not None
    expired = await app.request_async(
        f"/api/chat/tasks/{first['task']['task_id']}/replay-snapshot"
    )
    # The coordinator and replay buffer both retain 100 terminal tasks. Once
    # the coordinator evicts the record, the established API returns not-found.
    assert expired.status_code == 404, expired.text
    assert expired.json()["error"] == "ExecutionTaskNotFound"
    missing = await app.request_async(f"/api/tasks/{first['task']['task_id']}")
    assert missing.status_code == 404
    history = app.request(
        f"/api/chat/sessions/{first['session_id']}", params={"vault_name": vault.name}
    )
    assert history.status_code == 200
    assert [row["role"] for row in history.json()["messages"]] == ["user", "assistant"]


async def test_cursor_expiry_after_stream_admission_requests_resync(
    app: Application,
) -> None:
    from fastapi.responses import StreamingResponse

    from api.endpoints import chat_task_events
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from validation.scenarios.support.chat_submission_replay_contracts import (
        start_turn,
        submission_provider,
    )
    from validation.scenarios.support.chat_task_journeys import event_payloads

    vault = app.vault("AdmittedReplayCursorVault")
    await app.start()
    with submission_provider(app) as server:
        task_id = await start_turn(app, vault.name, "admitted-cursor", "FIRST_GATE")
        assert await asyncio.to_thread(server.started.wait, 30)
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            response = await chat_task_events(task_id, after_sequence=0)
        assert isinstance(response, StreamingResponse) and response.status_code == 200
        server.mode = "overflow"
        server.release.set()
        assert (await app.wait_task(task_id))["status"] == "completed"
        chunks = [chunk async for chunk in response.body_iterator]
        text = "".join(
            chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks
        )
        events = event_payloads(text)
        assert len(events) == 1
        assert events[0]["event"] == "chat_event_cursor_expired"
        assert events[0]["after_sequence"] == 0
        assert events[0]["oldest_available_sequence"] > 1
        assert events[0]["latest_sequence"] >= events[0]["oldest_available_sequence"]


async def test_pending_review_does_not_publish_a_fork_boundary(
    app: Application,
) -> None:
    from validation.scenarios.support.active_tool_cancellation import active_provider
    from validation.scenarios.support.inline_edit_reviews import request_review

    vault = app.vault("PendingReviewForkVault")
    await app.start()
    with active_provider(app) as server:
        server.mode = "review"
        session = "pending-review-fork"
        review = await request_review(
            app,
            server,
            vault.name,
            session,
            {
                "operation": "write",
                "path": "pending.md",
                "content": "PENDING_EFFECT",
            },
        )
        assert review["approvals"]
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200
        pending_rows = [
            row for row in detail.json()["messages"] if row["tool_call_ids"]
        ]
        assert pending_rows
        assert all(row["fork_sequence_index"] is None for row in pending_rows)
        rejected = app.request(
            f"/api/chat/sessions/{session}/fork",
            "POST",
            {
                "vault_name": vault.name,
                "through_sequence_index": pending_rows[-1]["through_sequence_index"],
            },
        )
        assert rejected.status_code >= 400
        assert not (vault / "pending.md").exists()
