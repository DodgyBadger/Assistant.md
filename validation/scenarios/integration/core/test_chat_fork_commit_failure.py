"""A real persistence failure cannot expose a partial chat fork point."""

import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_stream_provider import streaming_provider
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_failed_chat_commit_does_not_publish_fork_or_future_history(
    app: Application,
) -> None:
    vault = app.vault("ChatForkCommitFailureVault")
    await app.start()
    from core.runtime.state import get_runtime_context

    with streaming_provider() as server:
        configure_provider(app, server)
        server.release.set()
        session = "fork-commit-failure"
        get_runtime_context().chat_store.ensure_session(
            session, vault.name, owner_principal_id="local-user"
        )
        with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
            conn.execute(
                "CREATE TRIGGER reject_chat_response BEFORE INSERT ON chat_messages WHEN NEW.session_id = 'fork-commit-failure' AND NEW.role = 'assistant' BEGIN SELECT RAISE(ABORT, 'controlled response commit failure'); END"
            )
        failed = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Persist a response.",
                "model": "session-ops-model",
            },
        )
        assert (
            failed["terminal_event"]["event"] == "error"
            and failed["task"]["status"] == "failed"
        )
        raw = get_runtime_context().chat_store.get_stored_messages(
            session, vault.name, mode="raw"
        )
        assert len(raw) == 1 and raw[0].role == "user"
        snapshot = await app.request_async(
            f"/api/chat/tasks/{failed['task']['task_id']}/replay-snapshot"
        )
        assert snapshot.status_code == 200
        assert all(
            row.get("fork_sequence_index") is None for row in snapshot.json()["events"]
        )
        before = app.request(
            "/api/chat/sessions", params={"vault_name": vault.name}
        ).json()
        rejected = await app.request_async(
            f"/api/chat/sessions/{session}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 1},
        )
        assert rejected.status_code >= 400
        after = app.request(
            "/api/chat/sessions", params={"vault_name": vault.name}
        ).json()
        assert after == before
        detail = app.request(
            f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
        )
        assert detail.status_code == 200 and len(detail.json()["messages"]) == 1
