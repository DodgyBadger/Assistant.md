"""SQLite observations and deliberately incomplete persisted input for chat journeys."""

from __future__ import annotations

import sqlite3
from typing import Any

from validation.scenarios.application import Application


def message_rows(
    app: Application, vault_name: str, session_id: str
) -> list[tuple[Any, ...]]:
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        return conn.execute(
            """SELECT sequence_index, role, content_text, message_json
               FROM chat_messages WHERE session_id = ? AND vault_name = ?
               ORDER BY sequence_index""",
            (session_id, vault_name),
        ).fetchall()


def tool_rows(
    app: Application, vault_name: str, session_id: str
) -> list[tuple[Any, ...]]:
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        session = conn.execute(
            "SELECT session_id, vault_name FROM chat_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        assert session == (session_id, vault_name)
        return conn.execute(
            """SELECT event_type, tool_name, result_text, artifact_ref, tool_call_id
               FROM chat_tool_events WHERE session_id = ? AND vault_name = ?
               ORDER BY id""",
            (session_id, vault_name),
        ).fetchall()


def seed_uncommitted_tool_call(
    app: Application, vault_name: str, session_id: str
) -> None:
    """Seed interrupted-turn input; this does not test cancellation writes."""
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        conn.execute(
            """INSERT INTO chat_tool_events
               (session_id, vault_name, tool_call_id, tool_name, event_type, args_json, result_text)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                vault_name,
                "cancelled-turn-call",
                "chat_history_compact",
                "call",
                '{"cancelled":true}',
                None,
            ),
        )


HISTORY_CODE = """
history = await retrieve_history(scope="session", limit="all")
user_prompts = []
for item in history.items:
    try:
        role = item.role
        content = item.content
    except AttributeError:
        continue
    if role == "user":
        user_prompts.append(content)
{
    "source": history.source,
    "history_source": history.metadata.get("canonical_source", ""),
    "history_message_filter": history.metadata.get("message_filter", ""),
    "item_count": history.item_count,
    "last_content": history.items[-1].content if history.items else "",
    "user_prompts": user_prompts,
}
"""


PERSISTENCE_CONTEXT = """---
run_type: context
description: Full canonical history for the persistence contract
---
```python
history = await retrieve_history(scope="session", limit="all")
await assemble_context(history=history.items)
```
"""
