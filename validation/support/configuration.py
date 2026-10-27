"""Isolated configuration fixtures without exposing bulk runtime mutation APIs."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

from core.settings import repository
from core.settings.store import SETTINGS_TEMPLATE, SettingsFile, refresh_settings_cache


def read_configuration(root: Path) -> dict[str, Any]:
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


def seed_configuration(root: Path) -> None:
    """Seed a scenario's explicit database even while a prior runtime exists."""
    conn = repository.connect(root)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            current = repository.revision(conn)
            raw = (
                repository.read_records(conn)
                if current is not None
                else yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8"))
            )
            if "test" in raw.get("models", {}):
                return
            if current is None:
                # Stable explicit fixture policy: tests opt into scheduler scans and
                # unconfigured-model setup independently of shipped defaults.
                raw["settings"]["default_model"]["value"] = "sonnet"
                raw["settings"]["vault_scan_interval_seconds"]["value"] = 0
            raw.setdefault("providers", {})["test"] = {
                "api_key": None,
                "base_url": None,
                "user_editable": True,
            }
            raw.setdefault("models", {})["test"] = {
                "provider": "test",
                "model_string": "test",
                "capabilities": ["text"],
                "user_editable": True,
            }
            repository.write_records(conn, raw)
            if current is None:
                conn.execute(
                    "INSERT INTO configuration_state (singleton, revision, source) "
                    "VALUES (1, 1, 'validation_fixture')"
                )
            else:
                conn.execute(
                    "UPDATE configuration_state SET revision = ? WHERE singleton = 1",
                    (current + 1,),
                )
    finally:
        conn.close()
    refresh_settings_cache()
