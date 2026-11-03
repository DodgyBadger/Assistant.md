"""Real broken transcript FTS input and private diagnostic projection input."""

from __future__ import annotations

import sqlite3

PRIVATE_EXCEPTION = "PRIVATE_TRANSCRIPT_ERROR_PAYLOAD"
PRIVATE_QUERY = "private transcript query"


def project_private_error():
    from core.tools.session_ops import _session_failure_diagnostics

    try:
        raise sqlite3.OperationalError(PRIVATE_EXCEPTION)
    except sqlite3.OperationalError as error:
        assert PRIVATE_EXCEPTION in str(error)
        return _session_failure_diagnostics(error)


def break_transcript_content(app):
    with sqlite3.connect(app.root / "system/chat_sessions.db") as conn:
        source_row = conn.execute(
            "SELECT id FROM chat_messages WHERE session_id='source' ORDER BY sequence_index LIMIT 1"
        ).fetchone()
        assert source_row is not None
        conn.execute("DROP TABLE chat_messages_fts")
        conn.execute(
            "CREATE VIRTUAL TABLE chat_messages_fts USING fts5(content_text, content='unavailable_transcript_content')"
        )
        conn.execute(
            "INSERT INTO chat_messages_fts(rowid, content_text) VALUES (?, ?)",
            (source_row[0], PRIVATE_QUERY),
        )
        try:
            conn.execute(
                "SELECT snippet(chat_messages_fts,0,'[',']','...',32) FROM chat_messages_fts WHERE chat_messages_fts MATCH ?",
                (PRIVATE_QUERY,),
            ).fetchall()
        except sqlite3.OperationalError as error:
            app.evidence.save_json(
                "transcript-sqlite-failure-input.json",
                {
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "scope": "Actual SQLite failure is generic; private exception text is separately supplied to the same application diagnostic projector",
                },
            )
        else:
            raise AssertionError("External FTS content must fail on actual retrieval")
