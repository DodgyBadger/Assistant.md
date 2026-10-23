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


def test_import_backup_matches_the_exact_snapshot_read(isolated_store, monkeypatch):
    legacy = isolated_store / "settings.yaml"
    original = b"settings:\n  debug:\n    value: false\n"
    legacy.write_bytes(original)
    prepare_backup = store.prepare_migration_backup_path

    def concurrent_source_edit(root, filename):
        legacy.write_text("settings:\n  debug:\n    value: true\n")
        return prepare_backup(root, filename)

    monkeypatch.setattr(store, "prepare_migration_backup_path", concurrent_source_edit)
    assert store.load_settings().settings["debug"].value is False
    backup = next((isolated_store / "migration_backups").glob("settings.yaml.bak*"))
    assert backup.read_bytes() == original


def test_interrupted_import_rolls_back_initialization_and_records(
    isolated_store, monkeypatch
):
    from core.settings import repository

    legacy = isolated_store / "settings.yaml"
    legacy.write_text("settings:\n  debug:\n    value: false\n")
    write_records = repository.write_records

    def interrupt(conn, data):
        write_records(conn, data)
        raise RuntimeError("interrupted import")

    with monkeypatch.context() as scoped:
        scoped.setattr(repository, "write_records", interrupt)
        with pytest.raises(RuntimeError, match="interrupted import"):
            store.load_settings()
    with repository.connect(isolated_store) as conn:
        assert repository.revision(conn) is None
        assert all(not entries for entries in repository.read_records(conn).values())
    assert store.load_settings().settings["debug"].value is False


def test_validation_seeding_uses_explicit_root_not_active_bootstrap_root(
    isolated_store,
):
    from validation.core.configuration import seed_configuration

    fixture_root = isolated_store / "scenario-installation"
    seed_configuration(fixture_root)
    from core.settings.repository import connect, read_records

    conn = connect(fixture_root)
    try:
        assert "test" in read_records(conn)["models"]
    finally:
        conn.close()
    assert not (isolated_store / "settings.db").exists()


def test_legacy_jev_and_unused_typesafe_retire_during_import(isolated_store):
    original = yaml.safe_dump(
        {
            "models": {
                "jev": {
                    "provider": "typesafe",
                    "model_string": "jev-latest",
                    "capabilities": ["decision"],
                    "user_editable": False,
                }
            },
            "providers": {
                "typesafe": {"api_key": "TYPESAFE_API_KEY", "user_editable": False}
            },
        }
    )
    (isolated_store / "settings.yaml").write_text(original)
    loaded = store.load_settings()
    assert "jev" not in loaded.models
    assert "typesafe" not in loaded.providers
    backup = next((isolated_store / "migration_backups").glob("settings.yaml.bak*"))
    assert backup.read_text() == original
    store.refresh_settings_cache()
    assert "jev" not in store.load_settings().models
    assert "typesafe" not in store.load_settings().providers


def test_corrupt_database_emits_failure_without_modifying_source(isolated_store):
    import json
    import sqlite3

    store.load_settings()
    store.refresh_settings_cache()
    database = isolated_store / "settings.db"
    original = b"not sqlite"
    database.write_bytes(original)
    with pytest.raises(sqlite3.DatabaseError):
        store.load_settings()
    assert database.read_bytes() == original
    events = [
        json.loads(line)
        for line in (isolated_store / "activity.log").read_text().splitlines()
    ]
    failures = [
        event["data"]
        for event in events
        if event.get("data", {}).get("event") == "settings_database_load_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["status"] == "failed"
    assert failures[0]["database_path"] == str(database)
    assert failures[0]["error_type"] == "DatabaseError"
