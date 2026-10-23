"""Durable configuration migration, isolation, and transaction contracts."""

from copy import deepcopy

import pytest
import yaml

from core.settings import store


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    from core.runtime import paths

    monkeypatch.setattr(paths, "_bootstrap_system_root", tmp_path)
    monkeypatch.setattr(paths, "_bootstrap_data_root", tmp_path / "data")
    monkeypatch.setattr(store, "_resolve_system_root", lambda: tmp_path)
    store.refresh_settings_cache()
    yield tmp_path
    store.refresh_settings_cache()


def test_legacy_import_is_backed_up_once_and_yaml_becomes_non_authoritative(
    isolated_store,
):
    root = isolated_store
    legacy = root / "settings.yaml"
    original = {
        "settings": {"debug": {"value": False}},
        "models": {},
        "providers": {},
        "tools": {},
    }
    legacy.write_text(yaml.safe_dump(original))
    loaded = store.load_settings()
    assert (root / "settings.db").exists()
    assert loaded.settings["debug"].value is False
    assert legacy.read_text() == yaml.safe_dump(original)
    backups = list((root / "migration_backups").glob("settings.yaml.bak*"))
    assert len(backups) == 1
    assert yaml.safe_load(backups[0].read_text()) == original
    legacy.write_text("invalid: [")
    store.refresh_settings_cache()
    assert store.load_settings().settings["debug"].value is False
    assert len(list((root / "migration_backups").glob("settings.yaml.bak*"))) == 1


def test_new_install_has_no_runtime_yaml_and_stale_writes_are_rejected(isolated_store):
    first = store.load_settings()
    assert not (isolated_store / "settings.yaml").exists()
    stale = first.model_copy(deep=True)
    first.settings["debug"].value = False
    store.save_settings(first)
    stale.settings["debug"].value = True
    with pytest.raises(ValueError, match="reload|Reload"):
        store.save_settings(stale)
    assert store.load_settings().settings["debug"].value is False


def test_invalid_import_is_retryable_and_does_not_seed_defaults(isolated_store):
    legacy = isolated_store / "settings.yaml"
    legacy.write_text("models: []\n")
    with pytest.raises(ValueError):
        store.load_settings()
    legacy.write_text("settings:\n  debug:\n    value: false\n")
    assert store.load_settings().settings["debug"].value is False


def test_failed_write_leaves_persisted_state_and_cached_reads_unchanged(
    isolated_store, monkeypatch
):
    from core.settings import repository

    settings = store.load_settings()
    original = deepcopy(settings.model_dump())
    settings.settings["debug"].value = not settings.settings["debug"].value
    original_write = repository.write_records

    def fail_after_records(conn, data):
        original_write(conn, data)
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(repository, "write_records", fail_after_records)
    with pytest.raises(RuntimeError, match="injected"):
        store.save_settings(settings)
    assert store.load_settings().model_dump() == original
