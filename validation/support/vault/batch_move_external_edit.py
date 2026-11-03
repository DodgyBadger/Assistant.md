"""Real SQLite workload and independent filesystem observer for move races."""

import sqlite3
import time
from contextlib import contextmanager
from threading import Event, Thread


@contextmanager
def delay_move_audit_and_edit(app, target):
    with sqlite3.connect(app.root / "system/vault_state.db") as conn:
        conn.execute(
            "CREATE TRIGGER delay_second_move BEFORE INSERT ON vault_mutations WHEN NEW.operation='move' AND NEW.path='Two/b.md' BEGIN SELECT sum(x) FROM (WITH RECURSIVE wait(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM wait WHERE x<20000000) SELECT x FROM wait); SELECT RAISE(ABORT, 'controlled delayed audit failure'); END"
        )
    observed = Event()
    stop = Event()
    errors = []

    def edit():
        deadline = time.monotonic() + 30
        while not stop.is_set() and time.monotonic() < deadline:
            if target.exists():
                try:
                    target.write_text("NEWER_EXTERNAL_EDIT", encoding="utf-8")
                    observed.set()
                except OSError as exc:
                    errors.append(exc)
                return
            time.sleep(0.001)

    thread = Thread(target=edit, daemon=True)
    thread.start()
    try:
        yield observed, errors
    finally:
        stop.set()
        thread.join(5)
        assert not thread.is_alive()
        with sqlite3.connect(app.root / "system/vault_state.db") as conn:
            conn.execute("DROP TRIGGER delay_second_move")
