# Deferred reference only; see validation/registry/pytest-gaps.json.
# Original complete source is available in Git before the 2026-10-31 cleanup.
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
