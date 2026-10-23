"""Default inheritance, explicit intent, and settings upgrade contracts."""

import json
import sqlite3

import pytest
import yaml

from core.settings import schema, store


@pytest.fixture
def installation(tmp_path, monkeypatch):
    from core.runtime import paths

    monkeypatch.setattr(paths, "_bootstrap_system_root", tmp_path)
    monkeypatch.setattr(paths, "_bootstrap_data_root", tmp_path / "data")
    template = tmp_path / "defaults.yaml"
    template.write_text(
        yaml.safe_dump(
            {
                "settings": {
                    "limit": {
                        "value": 3,
                        "description": "Current metadata",
                        "category": "General",
                    }
                },
                "models": {},
                "providers": {},
                "tools": {},
            }
        )
    )
    monkeypatch.setattr(store, "SETTINGS_TEMPLATE", template)
    store.refresh_settings_cache()
    yield tmp_path, template
    store.refresh_settings_cache()


def test_inherited_default_tracks_packaged_value_but_explicit_equal_override_does_not(
    installation,
):
    root, template = installation
    snapshot = store.load_settings()
    assert snapshot.settings["limit"].source == "default"
    snapshot.settings["limit"].source = "custom"
    store.save_settings(snapshot)
    template.write_text(template.read_text().replace("value: 3", "value: 4"))
    store.refresh_settings_cache()
    assert store.load_settings().settings["limit"].value == 3
    snapshot = store.load_settings()
    del snapshot.settings["limit"]
    store.save_settings(snapshot)
    assert store.load_settings().settings["limit"].value == 4
    assert store.load_settings().settings["limit"].source == "default"
    with sqlite3.connect(root / "settings.db") as conn:
        assert (
            conn.execute("SELECT count(*) FROM configuration_settings").fetchone()[0]
            == 0
        )


def test_v1_values_remain_custom_and_receive_a_pre_upgrade_backup(installation):
    root, _ = installation
    conn = sqlite3.connect(root / "settings.db")
    schema._create_tables(conn)
    conn.execute(
        "CREATE TABLE schema_migrations(namespace TEXT, version INTEGER, name TEXT, PRIMARY KEY(namespace,version))"
    )
    conn.execute(
        "INSERT INTO schema_migrations VALUES('settings',1,'create_configuration_records')"
    )
    conn.execute("INSERT INTO configuration_state VALUES(1,7,'defaults')")
    conn.execute(
        "INSERT INTO configuration_settings VALUES(?,?)",
        ("limit", json.dumps({"value": 3, "description": "Old metadata"})),
    )
    conn.commit()
    conn.close()
    loaded = store.load_settings()
    assert loaded.settings["limit"].source == "custom"
    assert loaded.settings["limit"].description == "Current metadata"
    backups = list((root / "migration_backups").glob("settings.db*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert (
            conn.execute("SELECT revision FROM configuration_state").fetchone()[0] == 7
        )
        assert (
            conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            == 1
        )
    with sqlite3.connect(root / "settings.db") as conn:
        assert (
            conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            == 2
        )
    store.refresh_settings_cache()
    store.load_settings()
    assert len(list((root / "migration_backups").glob("settings.db*"))) == 1


def test_newer_settings_version_is_rejected_without_rewriting_database(installation):
    root, _ = installation
    store.load_settings()
    with sqlite3.connect(root / "settings.db") as conn:
        conn.execute(
            "INSERT INTO schema_migrations(namespace,version,name) VALUES('settings',999,'future')"
        )
    original = (root / "settings.db").read_bytes()
    store.refresh_settings_cache()
    with pytest.raises(ValueError, match="newer|unsupported"):
        store.load_settings()
    assert (root / "settings.db").read_bytes() == original


def test_failed_upgrade_keeps_old_checkpoint_and_can_retry(installation, monkeypatch):
    from core.database_migrations import SQLiteMigration

    root, _ = installation
    store.load_settings()
    with sqlite3.connect(root / "settings.db") as conn:
        revision = conn.execute("SELECT revision FROM configuration_state").fetchone()[
            0
        ]

    def fail(conn):
        conn.execute("UPDATE configuration_state SET revision=12345")
        raise RuntimeError("injected upgrade failure")

    original = schema.SETTINGS_MIGRATIONS
    with monkeypatch.context() as scoped:
        scoped.setattr(
            schema,
            "SETTINGS_MIGRATIONS",
            (*original, SQLiteMigration(3, "test_upgrade", fail)),
        )
        store.refresh_settings_cache()
        with pytest.raises(schema.SettingsMigrationError):
            store.load_settings()
    with sqlite3.connect(root / "settings.db") as conn:
        assert (
            conn.execute("SELECT revision FROM configuration_state").fetchone()[0]
            == revision
        )
        assert (
            conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            == 2
        )
    applied = []
    with monkeypatch.context() as scoped:
        scoped.setattr(
            schema,
            "SETTINGS_MIGRATIONS",
            (
                *original,
                SQLiteMigration(3, "test_upgrade", lambda conn: applied.append(True)),
            ),
        )
        store.load_settings()
    assert applied == [True]
    with sqlite3.connect(root / "settings.db") as conn:
        assert (
            conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0]
            == 3
        )


def test_reset_one_setting_preserves_other_overrides_and_validates_dependencies(
    installation, monkeypatch
):
    from core.settings import config_editor

    root, _ = installation
    monkeypatch.setattr(
        config_editor, "refresh_configuration_status_cache", lambda: None
    )
    snapshot = store.load_settings()
    snapshot.settings["limit"].source = "custom"
    snapshot.settings["limit"].value = 9
    store.save_settings(snapshot)
    restored = config_editor.restore_general_setting("limit")
    assert restored.source == "default" and restored.value == 3
    with sqlite3.connect(root / "settings.db") as conn:
        assert (
            conn.execute("SELECT count(*) FROM configuration_settings").fetchone()[0]
            == 0
        )


def test_reset_rejects_an_invalid_default_relationship_atomically(
    installation, monkeypatch
):
    from core.settings import config_editor

    root, template = installation
    data = yaml.safe_load(template.read_text())
    data["settings"] = {
        "compaction_strategy": {"value": "session_map"},
        "compaction_high_watermark_tokens": {"value": 80000},
        "compaction_low_watermark_tokens": {"value": 65000},
    }
    template.write_text(yaml.safe_dump(data))
    snapshot = store.load_settings()
    for name, value in {
        "compaction_high_watermark_tokens": 100000,
        "compaction_low_watermark_tokens": 90000,
    }.items():
        snapshot.settings[name].source = "custom"
        snapshot.settings[name].value = value
    store.save_settings(snapshot)
    before = store.load_settings().model_dump()
    monkeypatch.setattr(
        config_editor, "refresh_configuration_status_cache", lambda: None
    )
    with pytest.raises(Exception, match="less than"):
        config_editor.restore_general_setting("compaction_high_watermark_tokens")
    assert store.load_settings().model_dump() == before
