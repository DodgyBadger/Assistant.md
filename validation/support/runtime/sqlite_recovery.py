"""Create a genuine interrupted SQLite transaction for startup recovery scenarios."""

import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from validation.support.core_processes import declare_core_command

_CRASH_WRITE = """
import os, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA journal_mode=DELETE')
conn.execute('PRAGMA synchronous=FULL')
conn.execute('PRAGMA cache_size=1')
conn.execute('PRAGMA cache_spill=2')
conn.execute('BEGIN IMMEDIATE')
conn.execute("UPDATE recovery_probe SET value = 'uncommitted'")
os._exit(23)
"""


def interrupt_database_transaction(root: Path) -> Path:
    from core.vault_state.schema import ensure_vault_state_schema

    root.mkdir(parents=True, exist_ok=True)
    ensure_vault_state_schema(str(root))
    database = root / "vault_state.db"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute(
            "CREATE TABLE recovery_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL, padding BLOB)"
        )
        conn.executemany(
            "INSERT INTO recovery_probe(value,padding) VALUES ('committed',zeroblob(8192))",
            [()] * 32,
        )
        conn.commit()
    result = subprocess.run(
        declare_core_command([sys.executable, "-c", _CRASH_WRITE, str(database)]),
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 23, result.stderr.decode()
    journal = database.with_name(database.name + "-journal")
    assert journal.exists()
    assert journal.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7")
    return database


def committed_probe_values(database: Path) -> list[str]:
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as conn:
        return [
            row[0]
            for row in conn.execute("SELECT value FROM recovery_probe ORDER BY id")
        ]
