"""Ordered settings schema/content upgrades and their backup boundary."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml

from core.database import connect_sqlite_from_system_db, get_system_database_path
from core.database_migrations import SQLiteMigration, apply_sqlite_migrations
from core.migration_backups import prepare_migration_backup_path
from core.settings.upgrades import upgrade_general_settings

DB_NAME = "settings"
MIGRATION_NAMESPACE = "settings"
SECTIONS = ("settings", "models", "providers", "tools")


class SettingsMigrationError(ValueError):
    """Controlled upgrade failure that never copies stored configuration inputs."""

    def __init__(
        self, phase: str, version: int, backup_path: Path | None, error_type: str
    ):
        self.phase = phase
        self.version = version
        self.backup_path = backup_path
        self.error_type = error_type
        super().__init__(
            "Settings upgrade failed; inspect its phase and pre-upgrade backup, then retry."
        )


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS configuration_state "
        "(singleton INTEGER PRIMARY KEY CHECK (singleton = 1), "
        "revision INTEGER NOT NULL, source TEXT NOT NULL)"
    )
    for section in SECTIONS:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS configuration_{section} "
            "(name TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)))"
        )


def _add_default_ownership(conn: sqlite3.Connection) -> None:
    # v1 could not distinguish untouched values from deliberate choices: pin all.
    raw: dict[str, Any] = {
        section: {
            name: json.loads(payload)
            for name, payload in conn.execute(
                f"SELECT name, payload FROM configuration_{section}"
            )
        }
        for section in SECTIONS
    }
    template = (
        yaml.safe_load((Path(__file__).parent / "settings.template.yaml").read_text())
        or {}
    )
    upgraded = upgrade_general_settings(raw, template)["settings"]
    conn.execute("DELETE FROM configuration_settings")
    conn.executemany(
        "INSERT INTO configuration_settings(name,payload) VALUES(?,?)",
        [
            (name, json.dumps({**entry, "source": "custom"}, allow_nan=False))
            for name, entry in upgraded.items()
        ],
    )
    conn.execute(
        "UPDATE configuration_state SET revision = revision + 1 WHERE singleton = 1"
    )


SETTINGS_MIGRATIONS = (
    SQLiteMigration(1, "create_configuration_records", _create_tables),
    SQLiteMigration(
        2, "pin_existing_values_and_upgrade_setting_contracts", _add_default_ownership
    ),
)


def _versions(conn: sqlite3.Connection) -> tuple[int, ...]:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone():
        return ()
    return tuple(
        row[0]
        for row in conn.execute(
            "SELECT version FROM schema_migrations WHERE namespace=? ORDER BY version",
            (MIGRATION_NAMESPACE,),
        )
    )


def _validate_versions(versions: tuple[int, ...]) -> None:
    supported = tuple(m.version for m in SETTINGS_MIGRATIONS)
    if any(version not in supported for version in versions):
        raise ValueError(
            "Settings database uses a newer or unsupported version; use a compatible application."
        )
    if versions != supported[: len(versions)]:
        raise ValueError(
            "Settings migration history is inconsistent; inspect the database and its backups."
        )


def ensure_settings_schema(
    system_root: str | None = None, *, backup: bool = True
) -> None:
    """Apply each checkpoint under a write fence, backing up initialized state first."""
    path = Path(get_system_database_path(DB_NAME, system_root))
    conn = connect_sqlite_from_system_db(DB_NAME, str(path.parent))
    backup_path: Path | None = None
    applied: list[int] = []
    phase = "inspect"
    migration_version = 0
    try:
        for migration in SETTINGS_MIGRATIONS:
            migration_version = migration.version
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                versions = _versions(conn)
                _validate_versions(versions)
                if migration.version in versions:
                    continue
                has_state = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='configuration_state'"
                ).fetchone()
                initialized = bool(
                    has_state
                    and conn.execute(
                        "SELECT 1 FROM configuration_state WHERE singleton=1"
                    ).fetchone()
                )
                if initialized and backup and backup_path is None:
                    phase = "backup"
                    backup_path = prepare_migration_backup_path(
                        path.parent, "settings.db.bak"
                    )
                    # A separate reader captures committed state while this writer
                    # fences competing mutations before the first migration step.
                    source = sqlite3.connect(str(path))
                    destination = sqlite3.connect(str(backup_path))
                    try:
                        source.backup(destination)
                    finally:
                        destination.close()
                        source.close()
                phase = "apply"
                apply_sqlite_migrations(
                    conn, namespace=MIGRATION_NAMESPACE, migrations=(migration,)
                )
                applied.append(migration.version)
                phase = "inspect"
    except ValueError:
        # Unsupported/inconsistent history is already a controlled inspection error.
        if phase == "inspect":
            raise
        raise SettingsMigrationError(
            phase, migration_version, backup_path, "ValueError"
        ) from None
    except Exception as exc:
        if phase == "inspect":
            raise
        raise SettingsMigrationError(
            phase, migration_version, backup_path, type(exc).__name__
        ) from None
    finally:
        conn.close()
    if applied and backup_path is not None:
        from core.logger import UnifiedLogger

        UnifiedLogger(tag="settings", default_sinks=["activity"]).info(
            "Settings upgrade completed",
            data={
                "event": "settings_upgrade_completed",
                "status": "completed",
                "database_path": str(path),
                "backup_path": str(backup_path),
                "versions": applied,
            },
        )
