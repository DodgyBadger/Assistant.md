"""Isolated configuration fixtures without exposing bulk runtime mutation APIs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from core.settings import repository
from core.settings.store import SettingsFile, load_settings, refresh_settings_cache


def read_configuration(root: Path) -> dict[str, Any]:
    load_settings()
    conn = repository.connect(root)
    try:
        with conn:
            return repository.read_records(conn)
    finally:
        conn.close()


def install_configuration(root: Path, data: dict[str, Any]) -> None:
    """Install raw legacy/corruption fixtures; only validation imports this helper."""
    conn = repository.connect(root)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            current = repository.revision(conn)
            if current is None:
                raise ValueError("Validation configuration must be initialized first")
            repository.write_records(conn, data)
            conn.execute(
                "UPDATE configuration_state SET revision = ? WHERE singleton = 1",
                (current + 1,),
            )
    finally:
        conn.close()
    refresh_settings_cache()


def validate_configuration(data: dict[str, Any]) -> None:
    from core.settings.model_registry import build_model_registry

    build_model_registry(SettingsFile.model_validate(data).models).validate()


def read_database_snapshot(path: Path) -> dict[str, Any]:
    """Read a live database without initializing or migrating the source."""
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return repository.read_records(conn)
    finally:
        conn.close()
