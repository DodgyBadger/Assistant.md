"""Transactional, per-record persistence for installation configuration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from core.database import connect_sqlite_from_system_db, get_system_database_path
from core.settings.schema import DB_NAME, SECTIONS, ensure_settings_schema


class StaleSettingsError(ValueError):
    """A configuration snapshot no longer represents the committed revision."""


def database_path(root: Path) -> Path:
    return Path(get_system_database_path(DB_NAME, str(root)))


def connect(root: Path) -> sqlite3.Connection:
    ensure_settings_schema(str(root))
    return connect_sqlite_from_system_db(DB_NAME, str(root))


def revision(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        "SELECT revision FROM configuration_state WHERE singleton = 1"
    ).fetchone()
    return int(row[0]) if row else None


def read_records(conn: sqlite3.Connection) -> dict[str, Any]:
    if not conn.in_transaction:
        conn.execute("BEGIN")
    return {
        section: {
            str(name): json.loads(payload)
            for name, payload in conn.execute(
                f"SELECT name, payload FROM configuration_{section} ORDER BY rowid"
            )
        }
        for section in SECTIONS
    }


def write_records(conn: sqlite3.Connection, data: dict[str, Any]) -> None:
    """Replace records inside the caller's transaction; never commit here."""
    for section in SECTIONS:
        existing = dict(
            conn.execute(f"SELECT name, payload FROM configuration_{section}")
        )
        incoming = {
            name: json.dumps(payload, allow_nan=False, sort_keys=True)
            for name, payload in data.get(section, {}).items()
        }
        conn.executemany(
            f"DELETE FROM configuration_{section} WHERE name = ?",
            [(name,) for name in existing.keys() - incoming.keys()],
        )
        conn.executemany(
            f"INSERT INTO configuration_{section} (name, payload) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload = excluded.payload",
            [
                (name, payload)
                for name, payload in incoming.items()
                if existing.get(name) != payload
            ],
        )


def save(root: Path, data: dict[str, Any], expected_revision: int) -> int:
    conn = connect(root)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            current = revision(conn)
            if current != expected_revision:
                raise StaleSettingsError(
                    "Settings changed since this edit began. Reload and retry."
                )
            write_records(conn, data)
            updated = expected_revision + 1
            conn.execute(
                "UPDATE configuration_state SET revision = ? WHERE singleton = 1",
                (updated,),
            )
        return updated
    finally:
        conn.close()


def backup(root: Path, target: Path) -> int | None:
    """Copy committed state and return the revision captured by that snapshot."""
    source = connect(root)
    try:
        # Pin the revision and backup to one read snapshot. A caller performing a
        # destructive operation must reject mutations newer than this revision.
        source.execute("BEGIN")
        captured_revision = revision(source)
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
        finally:
            destination.close()
        return captured_revision
    finally:
        source.close()
