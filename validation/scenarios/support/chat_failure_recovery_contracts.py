"""Real configuration and canonical input setup for chat failure recovery."""

import sqlite3


def set_setting(app, key, value):
    response = app.request(
        f"/api/system/settings/general/{key}", method="PUT", data={"value": value}
    )
    assert response.status_code == 200, response.text


def seed_corrupt_history(app, vault, session):
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    store.ensure_session(session, vault, owner_principal_id="local-user")
    store.add_messages(
        session,
        vault,
        [ModelRequest(parts=[UserPromptPart(content="Earlier durable request.")])],
    )
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        original = conn.execute(
            "SELECT message_json FROM chat_messages WHERE session_id=? AND vault_name=? AND sequence_index=0",
            (session, vault),
        ).fetchone()[0]
        conn.execute(
            "UPDATE chat_messages SET message_json=? WHERE session_id=? AND vault_name=? AND sequence_index=0",
            ("{", session, vault),
        )
    return original


def restore_history(app, vault, session, original):
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        conn.execute(
            "UPDATE chat_messages SET message_json=? WHERE session_id=? AND vault_name=? AND sequence_index=0",
            (original, session, vault),
        )
