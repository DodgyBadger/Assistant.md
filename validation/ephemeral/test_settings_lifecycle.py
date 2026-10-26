"""Default inheritance, explicit intent, and settings upgrade contracts."""

import sqlite3

import pytest
import yaml

from core.settings import store


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


def test_reset_service_exposes_backup_conflicts_without_resetting(
    installation, monkeypatch
):
    from unittest.mock import Mock

    from api.exceptions import InvalidSystemSettingError
    from api.services import configuration
    from core.settings import repository

    root, _ = installation
    store.load_settings()
    original_backup = repository.backup

    def backup_then_edit(system_root, target):
        captured = original_backup(system_root, target)
        edited = store.load_settings()
        edited.settings["limit"].source = "custom"
        edited.settings["limit"].value = 9
        store.save_settings(edited)
        return captured

    monkeypatch.setattr(configuration, "logger", Mock())
    monkeypatch.setattr(repository, "backup", backup_then_edit)
    reload = Mock()
    monkeypatch.setattr(configuration, "reload_configuration", reload)
    with pytest.raises(InvalidSystemSettingError, match="pre-reset backup"):
        configuration.restore_managed_configuration_defaults()
    assert store.load_settings().settings["limit"].value == 9
    reload.assert_not_called()
    assert len(list((root / "migration_backups").glob("settings.db.bak-*"))) == 1


def test_reset_service_allocates_distinct_backups(installation, monkeypatch):
    from unittest.mock import Mock

    from api.services import configuration
    from core.settings import config_editor

    root, template = installation
    monkeypatch.setattr(config_editor, "SETTINGS_TEMPLATE", template)
    monkeypatch.setattr(
        config_editor, "refresh_configuration_status_cache", lambda: None
    )
    monkeypatch.setattr(configuration, "logger", Mock())
    monkeypatch.setattr(configuration, "reload_configuration", Mock())
    store.load_settings()
    first = configuration.restore_managed_configuration_defaults()
    second = configuration.restore_managed_configuration_defaults()
    assert first.backup_path != second.backup_path
    backups = list((root / "migration_backups").glob("settings.db.bak-*"))
    assert len(backups) == 2
    with sqlite3.connect(first.backup_path) as conn:
        first_revision = conn.execute(
            "SELECT revision FROM configuration_state"
        ).fetchone()[0]
    with sqlite3.connect(second.backup_path) as conn:
        second_revision = conn.execute(
            "SELECT revision FROM configuration_state"
        ).fetchone()[0]
    assert second_revision == first_revision + 1
