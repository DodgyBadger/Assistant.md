"""Real SQLite and operating-system faults for Explorer recovery."""

import resource
import signal
import sqlite3
from contextlib import contextmanager


@contextmanager
def reject_second_move(app, source):
    with sqlite3.connect(app.root / "system/vault_state.db") as conn:
        conn.execute(
            "CREATE TRIGGER reject_second_batch_move BEFORE INSERT ON vault_mutations WHEN NEW.operation = 'move' AND NEW.path = "
            + "'"
            + source.replace("'", "''")
            + "'"
            + " BEGIN SELECT RAISE(ABORT, 'controlled move audit failure'); END"
        )
    try:
        yield
    finally:
        with sqlite3.connect(app.root / "system/vault_state.db") as conn:
            conn.execute("DROP TRIGGER reject_second_batch_move")


@contextmanager
def limit_file_writes(size):
    old_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
    old_signal = signal.getsignal(signal.SIGXFSZ)
    if old_limit[1] != resource.RLIM_INFINITY and old_limit[1] < size:
        raise RuntimeError("Hard file-size limit is below the required test limit")
    try:
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        resource.setrlimit(resource.RLIMIT_FSIZE, (size, old_limit[1]))
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, old_limit)
        signal.signal(signal.SIGXFSZ, old_signal)
