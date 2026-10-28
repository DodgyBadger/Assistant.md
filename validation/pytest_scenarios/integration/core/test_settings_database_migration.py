"""Settings persistence, migration recovery artifacts and schema admission."""

from __future__ import annotations

import json
import sqlite3

import pytest
import yaml

from core.database_migrations import apply_sqlite_migrations
from core.settings import schema as settings_schema
from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.settings import run_settings_process

pytestmark = pytest.mark.scenario


async def test_settings_numbered_upgrade(app: Application) -> None:
    app.vault("SettingsNumberedUpgrade")
    await app.start()
    # A database constraint fails the v2 replacement insert after its
    # delete, exercising transaction rollback and a subsequent retry.
    upgrade_root = app.root / "numbered-upgrade"
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
            conn.execute("SELECT revision FROM configuration_state").fetchone()[0] == 7
        )
        assert (
            conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            == 1
        )
        assert json.loads(
            conn.execute(
                "SELECT payload FROM configuration_settings WHERE name='debug'"
            ).fetchone()[0]
        ) == {"value": False, "description": "legacy"}
    with sqlite3.connect(upgrade_database) as conn:
        assert (
            conn.execute("SELECT revision FROM configuration_state").fetchone()[0] == 7
        )
        assert (
            conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            == 1
        )
        assert json.loads(
            conn.execute(
                "SELECT payload FROM configuration_settings WHERE name='debug'"
            ).fetchone()[0]
        ) == {"value": False, "description": "legacy"}
        conn.execute("DROP TRIGGER reject_upgrade")
    settings_schema.ensure_settings_schema(str(upgrade_root))
    backups = list((upgrade_root / "migration_backups").glob("settings.db.bak*"))
    assert len(backups) == 2
    successful_backup = next(path for path in backups if path != failed_backup)
    with sqlite3.connect(successful_backup) as conn:
        assert (
            conn.execute("SELECT revision FROM configuration_state").fetchone()[0] == 7
        )
        assert (
            conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
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
            conn.execute("SELECT revision FROM configuration_state").fetchone()[0] == 8
        )
        assert (
            conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            == 2
        )
        upgraded = json.loads(
            conn.execute(
                "SELECT payload FROM configuration_settings WHERE name='debug'"
            ).fetchone()[0]
        )
        assert upgraded["value"] is False and upgraded["source"] == "custom"
    run_settings_process(
        app,
        upgrade_root,
        """
from core.settings.store import load_settings, get_default_settings
entry = load_settings().settings['debug']
default = get_default_settings()['debug']
assert entry.value is False and entry.source == 'custom'
assert entry.description == default.description and entry.description != 'legacy'
""",
    )
    settings_schema.ensure_settings_schema(str(upgrade_root))
    assert len(list((upgrade_root / "migration_backups").glob("settings.db.bak*"))) == 2

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


async def test_settings_import_persistence_and_recovery(app: Application) -> None:
    app.vault("SettingsImportPersistence")
    await app.start()
    legacy_root = app.root / "legacy-installation"
    legacy_root.mkdir()
    original = yaml.safe_dump(
        {
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
    )
    (legacy_root / "settings.yaml").write_text(original)
    run_settings_process(
        app,
        legacy_root,
        """
import sqlite3
from core.settings import repository, store
first = store.load_settings()
assert first.models['custom'].alternate_aliases == ['historical']
assert first.models['custom'].managed_id is None
assert 'jev' not in first.models and 'typesafe' not in first.providers
legacy = Path("""
        + repr(str(legacy_root / "settings.yaml"))
        + """)
original = legacy.read_text()
backups = list((legacy.parent / 'migration_backups').glob('settings.yaml.bak*'))
assert len(backups) == 1 and backups[0].read_text() == original
legacy.write_text('malformed: [')
store.refresh_settings_cache()
assert store.load_settings().settings['debug'].value is False
stale = first.model_copy(deep=True)
first.settings['debug'].value = True
store.save_settings(first)
try:
    store.save_settings(stale)
except repository.StaleSettingsError:
    pass
else:
    raise AssertionError('Stale snapshot overwrote newer settings')
root = legacy.parent
committed = store.load_settings().model_dump(mode='json')
candidate = store.load_settings()
candidate.settings['debug'].value = False
with repository.connect(root) as conn:
    conn.execute("CREATE TRIGGER reject_save BEFORE INSERT ON configuration_settings BEGIN SELECT RAISE(ABORT, 'controlled save failure'); END")
try:
    store.save_settings(candidate)
except sqlite3.IntegrityError:
    pass
else:
    raise AssertionError('Constraint must reject the settings write')
assert store.load_settings().model_dump(mode='json') == committed
with repository.connect(root) as conn:
    conn.execute('DROP TRIGGER reject_save')
    persisted = repository.read_records(conn)
backup = root / 'snapshot.db'
repository.backup(root, backup)
with sqlite3.connect(backup) as conn:
    assert repository.read_records(conn) == persisted
assert len(list((root / 'migration_backups').glob('settings.yaml.bak*'))) == 1
""",
    )
    fresh_root = app.root / "fresh-installation"
    run_settings_process(
        app,
        fresh_root,
        """
from core.settings.store import load_settings
assert load_settings().models
""",
    )
    assert not (fresh_root / "settings.yaml").exists()

    failed_root = app.root / "failed-installation"
    failed_root.mkdir()
    (failed_root / "settings.yaml").write_text("models: []\n")
    run_settings_process(
        app,
        failed_root,
        """
from core.settings import repository, store
try:
    store.load_settings()
except ValueError:
    pass
else:
    raise AssertionError('Malformed legacy state must block initialization')
with repository.connect(Path("""
        + repr(str(failed_root))
        + """)) as conn:
    assert repository.revision(conn) is None
""",
    )
    repaired = yaml.safe_load(original)
    repaired["settings"]["debug"]["value"] = True
    (failed_root / "settings.yaml").write_text(yaml.safe_dump(repaired))
    run_settings_process(
        app,
        failed_root,
        """
from core.settings.store import load_settings
assert load_settings().settings['debug'].value is True
""",
    )
    corrupt_root = app.root / "corrupt-installation"
    corrupt_root.mkdir()
    database = corrupt_root / "settings.db"
    database.write_bytes(b"not sqlite")
    run_settings_process(
        app,
        corrupt_root,
        """
import sqlite3
from core.settings.store import load_settings
try:
    load_settings()
except sqlite3.DatabaseError:
    pass
else:
    raise AssertionError('Corrupt database must fail explicitly')
""",
    )
    assert database.read_bytes() == b"not sqlite"
    assert app.request("/api/system/settings").status_code == 404
    assert app.request("/api/system/settings", method="PUT", data={}).status_code == 404

    imported = [
        json.loads(line)["data"]
        for line in (legacy_root / "activity.log").read_text().splitlines()
        if json.loads(line).get("data", {}).get("event")
        == "settings_database_initialized"
    ]
    assert len(imported) == 1 and imported[0]["source"] == "legacy_yaml"
    failures = [
        json.loads(line)["data"]
        for line in (corrupt_root / "activity.log").read_text().splitlines()
        if json.loads(line).get("data", {}).get("event")
        == "settings_database_load_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["status"] == "failed"
    assert failures[0]["error_type"] == "DatabaseError"
    assert failures[0]["error"]
    initialization_failures = [
        json.loads(line)["data"]
        for line in (failed_root / "activity.log").read_text().splitlines()
        if json.loads(line).get("data", {}).get("event")
        == "settings_database_initialization_failed"
    ]
    assert initialization_failures
    app.evidence.save(
        "settings-migration-events.json",
        {
            "imported": imported,
            "load_failures": failures,
            "initialization_failures": initialization_failures,
        },
    )
