"""Pre-index native transcript inputs and post-DDL bookkeeping failure inputs."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)


def seed_pre_migration_transcript(root: Path, vault: str) -> list[tuple]:
    """Create the ownerless base schema, before migration 1 or any FTS objects.

    Canonical rows enter through legacy SQL, never modern ChatStore writes.
    Missing derived text also exercises migration 7's native-message projection.
    """
    root.mkdir(parents=True, exist_ok=True)
    adapter = TypeAdapter(ModelMessage)
    messages = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content="The cobalt lantern decision predates transcript indexing."
                )
            ]
        ),
        ModelResponse(
            parts=[TextPart(content="Preserve the oldest canonical answer.")]
        ),
    ]
    with sqlite3.connect(root / "chat_sessions.db") as conn:
        conn.executescript("""
            CREATE TABLE chat_sessions (
                session_id TEXT NOT NULL, vault_name TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_activity_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                title TEXT, metadata_json TEXT,
                PRIMARY KEY(session_id, vault_name));
            CREATE TABLE chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, vault_name TEXT NOT NULL,
                sequence_index INTEGER NOT NULL, direction TEXT NOT NULL,
                message_type TEXT NOT NULL, role TEXT NOT NULL,
                content_text TEXT, message_json TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(session_id, vault_name, sequence_index),
                FOREIGN KEY(session_id, vault_name)
                    REFERENCES chat_sessions(session_id, vault_name) ON DELETE CASCADE);
        """)
        conn.execute(
            "INSERT INTO chat_sessions(session_id,vault_name,title) VALUES ('oldest-transcript',?,'Legacy transcript')",
            (vault,),
        )
        for index, message in enumerate(messages):
            conn.execute(
                "INSERT INTO chat_messages(session_id,vault_name,sequence_index,direction,message_type,role,content_text,message_json) VALUES ('oldest-transcript',?,?,?,?,?,NULL,?)",
                (
                    vault,
                    index,
                    "inbound" if index == 0 else "outbound",
                    message.kind,
                    "user" if index == 0 else "assistant",
                    adapter.dump_json(message).decode(),
                ),
            )
    return canonical_message_rows(root)


def canonical_message_rows(root: Path) -> list[tuple]:
    with sqlite3.connect(root / "chat_sessions.db") as conn:
        return conn.execute(
            "SELECT id,session_id,vault_name,sequence_index,direction,message_type,role,message_json,created_at FROM chat_messages ORDER BY id"
        ).fetchall()


def summary_snapshot(database: Path) -> dict:
    with sqlite3.connect(database) as conn:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        return {
            name: conn.execute(f'SELECT * FROM "{name}" ORDER BY rowid').fetchall()
            for name in names
            # Compare logical FTS contents through its virtual table; SQLite owns
            # binary shadow-page layout and may rebuild it without changing data.
            if not name.startswith("session_summaries_fts_")
        }


@contextmanager
def reject_retirement_bookkeeping(database: Path):
    with sqlite3.connect(database) as conn:
        # This condition can fire only after retirement has actually dropped its
        # owned primary table, before the migration runner records completion.
        conn.execute(
            """CREATE TRIGGER reject_retirement_completion
            BEFORE INSERT ON schema_migrations
            WHEN NEW.namespace='session_summaries' AND NEW.version=4
              AND NOT EXISTS(SELECT 1 FROM sqlite_master WHERE name='session_summaries')
            BEGIN SELECT RAISE(ABORT, 'retirement bookkeeping rejected after table drops'); END"""
        )
    try:
        yield
    finally:
        with sqlite3.connect(database) as conn:
            conn.execute("DROP TRIGGER reject_retirement_completion")
