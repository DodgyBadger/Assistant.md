"""Fork creation rolls back fully and respects the selected history boundary."""

import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.compaction_journeys import add_turn

pytestmark = pytest.mark.scenario


async def test_fork_insert_failure_rolls_back_and_retry_excludes_future(
    app: Application,
) -> None:
    vault = app.vault("ChatForkAtomicFailureVault")
    await app.start()
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session = "fork-source"
    store.ensure_session(session, vault.name, owner_principal_id="local-user")
    add_turn(store, vault.name, session, "Earlier request.", "Earlier answer.")
    add_turn(store, vault.name, session, "Future request.", "Future answer.")
    initial = app.request("/api/chat/sessions", params={"vault_name": vault.name})
    assert initial.status_code == 200
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        conn.execute(
            "CREATE TRIGGER reject_fork_messages BEFORE INSERT ON chat_messages WHEN NEW.session_id != 'fork-source' BEGIN SELECT RAISE(ABORT, 'controlled fork insert failure'); END"
        )
    failure = await app.request_async(
        f"/api/chat/sessions/{session}/fork",
        method="POST",
        data={"vault_name": vault.name, "through_sequence_index": 1},
    )
    assert failure.status_code >= 400
    listing = app.request("/api/chat/sessions", params={"vault_name": vault.name})
    assert listing.status_code == 200 and listing.json() == initial.json()
    assert store.get_message_count(session, vault.name, mode="raw") == 4
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        conn.execute("DROP TRIGGER reject_fork_messages")
    retry = await app.request_async(
        f"/api/chat/sessions/{session}/fork",
        method="POST",
        data={"vault_name": vault.name, "through_sequence_index": 1},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["copied_message_count"] == 2
    child = retry.json()["session"]["session_id"]
    detail = app.request(
        f"/api/chat/sessions/{child}", params={"vault_name": vault.name}
    )
    assert detail.status_code == 200
    assert [row["content"] for row in detail.json()["messages"]] == [
        "Earlier request.",
        "Earlier answer.",
    ]
