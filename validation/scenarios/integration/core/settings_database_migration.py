"""One-time settings import, atomic edits, and SQLite recovery artifacts."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.database_migrations import apply_sqlite_migrations
from core.settings import repository, store
from core.settings import schema as settings_schema
from validation.core.base_scenario import BaseScenario


class SettingsDatabaseMigrationScenario(BaseScenario):
    async def test_scenario(self) -> None:
        self.create_vault("SettingsDatabaseMigration")
        await self.start_system()
        root = self._get_system_controller()._system_root
        fixture_root = root / "legacy-installation"
        fixture_root.mkdir()
        legacy = fixture_root / "settings.yaml"
        raw = {
            "settings": {"debug": {"value": False}},
            "providers": {
                "custom": {"base_url": "https://example.test"},
                "typesafe": {"api_key": "TYPESAFE_API_KEY", "user_editable": False},
            },
            "models": {
                "custom": {
                    "provider": "custom",
                    "model_string": "user-model",
                    "alternate_aliases": ["historical"],
                },
                "jev": {
                    "provider": "typesafe",
                    "model_string": "jev-latest",
                    "capabilities": ["decision"],
                    "user_editable": False,
                },
            },
            "tools": {},
        }
        original = yaml.safe_dump(raw)
        legacy.write_text(original)
        try:
            with patch.object(store, "_resolve_system_root", return_value=fixture_root):
                store.refresh_settings_cache()
                first = store.load_settings()
                assert first.models["custom"].alternate_aliases == ["historical"]
                assert first.models["custom"].managed_id is None
                assert "jev" not in first.models
                assert "typesafe" not in first.providers
                assert legacy.read_text() == original
                backups = list(
                    (fixture_root / "migration_backups").glob("settings.yaml.bak*")
                )
                assert len(backups) == 1
                assert backups[0].read_text() == original
                legacy.write_text("malformed: [")
                store.refresh_settings_cache()
                assert store.load_settings().settings["debug"].value is False
                stale = first.model_copy(deep=True)
                first.settings["debug"].value = True
                store.save_settings(first)
                try:
                    store.save_settings(stale)
                except repository.StaleSettingsError:
                    pass
                else:
                    raise AssertionError(
                        "Stale snapshots must never overwrite newer settings"
                    )
                committed = store.load_settings().model_dump(mode="json")
                candidate = store.load_settings()
                candidate.settings["debug"].value = False
                write_records = repository.write_records

                def failing_write(conn, data):
                    write_records(conn, data)
                    raise RuntimeError("injected transaction failure")

                with patch.object(
                    repository, "write_records", side_effect=failing_write
                ):
                    try:
                        store.save_settings(candidate)
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError("Injected write must fail")
                assert store.load_settings().model_dump(mode="json") == committed
                with repository.connect(fixture_root) as current_connection:
                    persisted = repository.read_records(current_connection)
                backup_path = fixture_root / "snapshot.db"
                repository.backup(fixture_root, backup_path)
                with sqlite3.connect(str(backup_path)) as conn:
                    assert repository.read_records(conn) == persisted
                assert (
                    len(
                        list(
                            (fixture_root / "migration_backups").glob(
                                "settings.yaml.bak*"
                            )
                        )
                    )
                    == 1
                )
            fresh_root = root / "fresh-installation"
            with patch.object(store, "_resolve_system_root", return_value=fresh_root):
                assert store.load_settings().models
                assert not (fresh_root / "settings.yaml").exists()
            failed_root = root / "failed-installation"
            failed_root.mkdir()
            failed_legacy = failed_root / "settings.yaml"
            failed_legacy.write_text("models: []\n")
            with patch.object(store, "_resolve_system_root", return_value=failed_root):
                try:
                    store.load_settings()
                except ValueError:
                    pass
                else:
                    raise AssertionError(
                        "Malformed legacy state must block initialization"
                    )
                with repository.connect(failed_root) as conn:
                    assert repository.revision(conn) is None
                repaired = deepcopy(raw)
                repaired["settings"]["debug"]["value"] = True
                failed_legacy.write_text(yaml.safe_dump(repaired))
                assert store.load_settings().settings["debug"].value is True
            corrupt_root = root / "corrupt-installation"
            corrupt_root.mkdir()
            corrupt_database = corrupt_root / "settings.db"
            corrupt_bytes = b"not sqlite"
            corrupt_database.write_bytes(corrupt_bytes)
            with patch.object(store, "_resolve_system_root", return_value=corrupt_root):
                try:
                    store.load_settings()
                except sqlite3.DatabaseError:
                    pass
                else:
                    raise AssertionError("Corrupt database must fail explicitly")
            assert corrupt_database.read_bytes() == corrupt_bytes

            # A database constraint fails the v2 replacement insert after its
            # delete, exercising transaction rollback and a subsequent retry.
            upgrade_root = root / "numbered-upgrade"
            upgrade_root.mkdir()
            upgrade_database = upgrade_root / "settings.db"
            with sqlite3.connect(upgrade_database) as conn:
                apply_sqlite_migrations(
                    conn,
                    namespace="settings",
                    migrations=settings_schema.SETTINGS_MIGRATIONS[:1],
                )
                conn.execute("INSERT INTO configuration_state VALUES(1, 7, 'defaults')")
                conn.execute(
                    "INSERT INTO configuration_settings VALUES(?, ?)",
                    ("debug", json.dumps({"value": False, "description": "legacy"})),
                )
                conn.execute(
                    "CREATE TRIGGER reject_upgrade BEFORE INSERT ON configuration_settings "
                    "BEGIN SELECT RAISE(ABORT, 'controlled upgrade failure'); END"
                )
            try:
                settings_schema.ensure_settings_schema(str(upgrade_root))
            except settings_schema.SettingsMigrationError as exc:
                assert exc.phase == "apply" and exc.version == 2
                failed_backup = exc.backup_path
                assert failed_backup is not None and failed_backup.exists()
            else:
                raise AssertionError("The SQLite constraint must fail the upgrade")
            with sqlite3.connect(failed_backup) as conn:
                assert (
                    conn.execute("SELECT revision FROM configuration_state").fetchone()[
                        0
                    ]
                    == 7
                )
                assert (
                    conn.execute(
                        "SELECT max(version) FROM schema_migrations"
                    ).fetchone()[0]
                    == 1
                )
                assert json.loads(
                    conn.execute(
                        "SELECT payload FROM configuration_settings WHERE name='debug'"
                    ).fetchone()[0]
                ) == {"value": False, "description": "legacy"}
            with sqlite3.connect(upgrade_database) as conn:
                assert (
                    conn.execute("SELECT revision FROM configuration_state").fetchone()[
                        0
                    ]
                    == 7
                )
                assert (
                    conn.execute(
                        "SELECT max(version) FROM schema_migrations"
                    ).fetchone()[0]
                    == 1
                )
                assert json.loads(
                    conn.execute(
                        "SELECT payload FROM configuration_settings WHERE name='debug'"
                    ).fetchone()[0]
                ) == {"value": False, "description": "legacy"}
                conn.execute("DROP TRIGGER reject_upgrade")
            settings_schema.ensure_settings_schema(str(upgrade_root))
            backups = list(
                (upgrade_root / "migration_backups").glob("settings.db.bak*")
            )
            assert len(backups) == 2
            successful_backup = next(path for path in backups if path != failed_backup)
            with sqlite3.connect(successful_backup) as conn:
                assert (
                    conn.execute("SELECT revision FROM configuration_state").fetchone()[
                        0
                    ]
                    == 7
                )
                assert (
                    conn.execute(
                        "SELECT max(version) FROM schema_migrations"
                    ).fetchone()[0]
                    == 1
                )
                assert (
                    json.loads(
                        conn.execute(
                            "SELECT payload FROM configuration_settings WHERE name='debug'"
                        ).fetchone()[0]
                    )["value"]
                    is False
                )
            with sqlite3.connect(upgrade_database) as conn:
                assert (
                    conn.execute("SELECT revision FROM configuration_state").fetchone()[
                        0
                    ]
                    == 8
                )
                assert (
                    conn.execute(
                        "SELECT max(version) FROM schema_migrations"
                    ).fetchone()[0]
                    == 2
                )
                upgraded = json.loads(
                    conn.execute(
                        "SELECT payload FROM configuration_settings WHERE name='debug'"
                    ).fetchone()[0]
                )
                assert upgraded["value"] is False and upgraded["source"] == "custom"
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import sys; "
                    "from core.runtime.paths import set_bootstrap_roots; "
                    "set_bootstrap_roots(Path(sys.argv[1]), Path(sys.argv[2])); "
                    "from core.settings.store import load_settings, get_default_settings; "
                    "entry = load_settings().settings['debug']; "
                    "default = get_default_settings()['debug']; "
                    "assert entry.value is False and entry.source == 'custom'; "
                    "assert entry.description == default.description and entry.description != 'legacy'",
                    str(upgrade_root / "data"),
                    str(upgrade_root),
                ],
                check=True,
                cwd=Path(__file__).resolve().parents[4],
                timeout=30,
            )
            settings_schema.ensure_settings_schema(str(upgrade_root))
            assert (
                len(list((upgrade_root / "migration_backups").glob("settings.db.bak*")))
                == 2
            )

            with sqlite3.connect(upgrade_database) as conn:
                conn.execute(
                    "INSERT INTO schema_migrations(namespace, version, name) VALUES('settings', 999, 'future')"
                )
            original_database = upgrade_database.read_bytes()
            try:
                settings_schema.ensure_settings_schema(str(upgrade_root))
            except ValueError:
                pass
            else:
                raise AssertionError("Future settings schema must be rejected")
            assert upgrade_database.read_bytes() == original_database
            assert self.call_api("/api/system/settings").status_code == 404
            assert (
                self.call_api("/api/system/settings", method="PUT", data={}).status_code
                == 404
            )
            with patch.object(
                repository,
                "save",
                side_effect=repository.StaleSettingsError(
                    "Settings changed since this edit began. Reload and retry."
                ),
            ):
                conflict = self.call_api(
                    "/api/system/settings/general/debug",
                    method="PUT",
                    data={"value": "false"},
                )
            assert conflict.status_code == 409, conflict.text
            assert conflict.json()["error"] == "SettingsConflict"
            events = [
                json.loads(line)
                for line in (root / "activity.log").read_text().splitlines()
            ]
            imported = [
                e["data"]
                for e in events
                if e.get("data", {}).get("event") == "settings_database_initialized"
                and e["data"]["database_path"] == str(fixture_root / "settings.db")
            ]
            assert len(imported) == 1 and imported[0]["source"] == "legacy_yaml"
            failures = [
                e["data"]
                for e in events
                if e.get("data", {}).get("event") == "settings_database_load_failed"
                and e["data"]["database_path"] == str(corrupt_database)
            ]
            assert len(failures) == 1
            assert failures[0]["status"] == "failed"
            assert failures[0]["error_type"] == "DatabaseError"
            assert failures[0]["error"]
            assert any(
                e.get("data", {}).get("event")
                == "settings_database_initialization_failed"
                for e in events
            )
        finally:
            store.refresh_settings_cache()
            await self.stop_system()
            self.teardown_scenario()
