"""Genuine SQLite constraints used to exercise fork persistence failures."""

import sqlite3
from contextlib import contextmanager


@contextmanager
def reject_fork_checkpoint_writes(app):
    app.evidence.register_secret("private fork failure marker")
    database = app.root / "system/chat_sessions.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TRIGGER reject_api_fork_checkpoint BEFORE INSERT ON chat_compaction_checkpoints BEGIN SELECT RAISE(ABORT, 'private fork failure marker'); END"
        )
    try:
        yield
    finally:
        with sqlite3.connect(database) as conn:
            conn.execute("DROP TRIGGER reject_api_fork_checkpoint")
