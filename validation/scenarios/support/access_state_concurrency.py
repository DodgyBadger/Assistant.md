"""SQLite transaction barriers and cleanup constraints for access-state journeys."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from threading import Event


@contextmanager
def reject_secret_cleanup(app):
    database = app.root / "system/access.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TRIGGER access_cleanup_failure BEFORE DELETE ON encrypted_secrets BEGIN SELECT RAISE(ABORT, 'controlled cleanup constraint'); END"
        )
    try:
        yield
    finally:
        with sqlite3.connect(database) as conn:
            conn.execute("DROP TRIGGER access_cleanup_failure")


def guarded_write_in_thread(service, owner, guard, target, started: Event):
    started.set()
    service.guarded_set_for_authority(
        authority=owner,
        guard=guard,
        expected_guard_value="old-guard",
        target=target,
        value="stale-write",
    )
