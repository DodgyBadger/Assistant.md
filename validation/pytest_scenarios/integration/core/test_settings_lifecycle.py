"""Effective defaults, reset ownership boundaries, and revision admission."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from core.settings import config_editor, repository
from core.settings.store import get_default_settings
from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.settings import (
    activity_events,
    general_settings,
)

pytestmark = pytest.mark.scenario


async def test_settings_lifecycle(app: Application) -> None:
    app.vault("SettingsLifecycleVault")
    app.evidence.register_secret("validation-fixture-only")
    await app.start()
    strategy = app.request(
        "/api/system/settings/general/compaction_strategy",
        method="PUT",
        data={"value": "recovery_card"},
    )
    assert strategy.status_code == 200, strategy.text
    before = general_settings(app)["default_api_timeout"]
    saved = app.request(
        "/api/system/settings/general/default_api_timeout",
        method="PUT",
        data={"value": before["value"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["source"] == "custom"
    other = app.request(
        "/api/system/settings/general/debug",
        method="PUT",
        data={"value": "true"},
    )
    assert other.status_code == 200, other.text
    reset = app.request(
        "/api/system/settings/general/default_api_timeout/override",
        method="DELETE",
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["source"] == "default"
    assert reset.json()["value"] == reset.json()["default_value"]
    assert "default_api_timeout" not in app.configuration()["settings"]
    assert general_settings(app)["debug"]["source"] == "custom"
    assert general_settings(app)["debug"]["value"] == "true"
    app.request("/api/system/settings/general/debug/override", method="DELETE")
    # Resetting one side of a numeric relationship must not make it invalid.
    high = app.request(
        "/api/system/settings/general/compaction_high_watermark_tokens",
        method="PUT",
        data={"value": "1"},
    )
    assert high.status_code == 200, high.text
    strategy = app.request(
        "/api/system/settings/general/compaction_strategy",
        method="PUT",
        data={"value": "session_map"},
    )
    assert strategy.status_code == 400
    app.request(
        "/api/system/settings/general/compaction_high_watermark_tokens/override",
        method="DELETE",
    )
    assert (
        app.request(
            "/api/system/settings/general/compaction_strategy",
            method="PUT",
            data={"value": "session_map"},
        ).status_code
        == 200
    )
    default_high = int(
        general_settings(app)["compaction_high_watermark_tokens"]["default_value"]
    )
    for key, value in (
        ("compaction_high_watermark_tokens", default_high + 20000),
        ("compaction_low_watermark_tokens", default_high + 10000),
    ):
        response = app.request(
            f"/api/system/settings/general/{key}",
            method="PUT",
            data={"value": str(value)},
        )
        assert response.status_code == 200, response.text
    before_invalid_reset = app.configuration()
    rejected = app.request(
        "/api/system/settings/general/compaction_high_watermark_tokens/override",
        method="DELETE",
    )
    assert rejected.status_code == 400, rejected.text
    assert app.configuration() == before_invalid_reset
    assert (
        int(general_settings(app)["compaction_high_watermark_tokens"]["value"])
        == default_high + 20000
    )
    assert (
        int(general_settings(app)["compaction_low_watermark_tokens"]["value"])
        == default_high + 10000
    )
    custom = app.request(
        "/api/system/models/my-lifecycle-model",
        method="PUT",
        data={
            "provider": "test",
            "model_string": "test",
            "alternate_aliases": ["historical-lifecycle-model"],
        },
    )
    assert custom.status_code == 200, custom.text
    secret = app.request(
        "/api/system/secrets",
        method="PUT",
        data={"name": "LIFECYCLE_SECRET", "value": "validation-fixture-only"},
    )
    assert secret.status_code == 200, secret.text
    authoring = (app.root / "system") / "Authoring" / "default.md"
    authoring_content = authoring.read_text() + "\n<!-- local customization -->\n"
    authoring.write_text(authoring_content)
    raw = app.configuration()
    raw["models"].pop("haiku")
    raw["models"]["read-only-custom-model"] = {
        **raw["models"]["my-lifecycle-model"],
        "alternate_aliases": [],
        "user_editable": False,
    }
    raw["providers"]["openai"]["base_url"] = "https://custom.example.test"
    raw["providers"]["my-lifecycle-provider"] = {
        "base_url": "https://custom.example.test",
        "user_editable": True,
    }
    raw["tools"]["my-lifecycle-tool"] = {
        **raw["tools"]["file_read"],
        "user_editable": True,
    }
    raw["settings"]["debug"] = {"value": True, "source": "custom"}
    app.install_configuration(raw)
    restored = app.request("/api/system/settings/defaults", method="POST")
    assert restored.status_code == 200, restored.text
    assert restored.json()["restart_required"] is True
    backup = Path(restored.json()["backup_path"])
    assert backup.exists()
    current = app.configuration()
    assert current["settings"] == {}
    assert authoring.read_text() == authoring_content
    assert (
        current["models"]["read-only-custom-model"]
        == raw["models"]["read-only-custom-model"]
    )
    assert (
        current["models"]["my-lifecycle-model"] == raw["models"]["my-lifecycle-model"]
    )
    assert all(
        current["providers"]["my-lifecycle-provider"][key] == value
        for key, value in raw["providers"]["my-lifecycle-provider"].items()
    )
    assert all(
        current["tools"]["my-lifecycle-tool"][key] == value
        for key, value in raw["tools"]["my-lifecycle-tool"].items()
    )
    assert current["models"]["haiku"]["managed_id"] == "builtin-haiku"
    assert current["providers"]["openai"]["base_url"] != "https://custom.example.test"
    assert all(entry["source"] == "default" for entry in general_settings(app).values())
    assert set(general_settings(app)) == set(get_default_settings())
    assert any(
        entry["name"] == "LIFECYCLE_SECRET" and entry["has_value"]
        for entry in app.request("/api/system/secrets").json()
    )
    with sqlite3.connect(backup) as conn:
        assert conn.execute(
            "SELECT payload FROM configuration_settings WHERE name='debug'"
        ).fetchone()
        first_revision = conn.execute(
            "SELECT revision FROM configuration_state"
        ).fetchone()[0]
    second_reset = app.request("/api/system/settings/defaults", method="POST")
    assert second_reset.status_code == 200, second_reset.text
    second_backup = Path(second_reset.json()["backup_path"])
    assert second_backup != backup and second_backup.exists()
    with sqlite3.connect(second_backup) as conn:
        assert (
            conn.execute("SELECT revision FROM configuration_state").fetchone()[0]
            == first_revision + 1
        )
    conflicting = deepcopy(current)
    conflicting["models"]["haiku"] = {
        "provider": "test",
        "model_string": "test",
        "user_editable": True,
    }
    app.install_configuration(conflicting)
    denied = app.request("/api/system/settings/defaults", method="POST")
    assert denied.status_code == 400, denied.text
    assert app.configuration() == conflicting
    app.install_configuration(current)
    events = activity_events(app)
    assert any(
        event.get("data", {}).get("event") == "general_setting_default_restored"
        for event in events
    )
    lifecycle = [
        event["data"]
        for event in events
        if event.get("data", {})
        .get("event", "")
        .startswith("managed_configuration_defaults_")
    ]
    starts = [event for event in lifecycle if event["status"] == "started"]
    terminals = [
        event for event in lifecycle if event["status"] in {"completed", "failed"}
    ]
    assert len(starts) == 3 and len(terminals) == 3
    assert {event["operation_id"] for event in starts} == {
        event["operation_id"] for event in terminals
    }
    assert sorted(event["status"] for event in terminals) == [
        "completed",
        "completed",
        "failed",
    ]
    assert all(
        "validation-fixture-only" not in json.dumps(event) for event in lifecycle
    )

    # This proves domain concurrency admission, not the API service's
    # internal backup-to-reset scheduling or error translation.
    root = app.root / "system"
    earlier = app.request(
        "/api/system/settings/general/default_api_timeout",
        method="PUT",
        data={"value": "11"},
    )
    assert earlier.status_code == 200, earlier.text
    backup_path = root / "lifecycle-concurrency-backup.db"
    backed_up_revision = repository.backup(root, backup_path)
    assert backed_up_revision is not None
    with sqlite3.connect(backup_path) as conn:
        assert repository.revision(conn) == backed_up_revision
        assert (
            repository.read_records(conn)["settings"]["default_api_timeout"]["value"]
            == 11
        )

    later = app.request(
        "/api/system/settings/general/default_api_timeout",
        method="PUT",
        data={"value": "12"},
    )
    assert later.status_code == 200, later.text
    edited = app.configuration()
    with sqlite3.connect(root / "settings.db") as conn:
        edited_revision = repository.revision(conn)
    assert edited_revision is not None
    assert edited_revision > backed_up_revision
    try:
        config_editor.reset_managed_configuration(expected_revision=backed_up_revision)
    except repository.StaleSettingsError:
        pass
    else:
        raise AssertionError("Reset must reject an edit newer than its backup")
    assert app.configuration() == edited
    assert general_settings(app)["default_api_timeout"]["value"] == "12"
    with sqlite3.connect(root / "settings.db") as conn:
        assert repository.revision(conn) == edited_revision
    with sqlite3.connect(backup_path) as conn:
        assert repository.revision(conn) == backed_up_revision
        assert (
            repository.read_records(conn)["settings"]["default_api_timeout"]["value"]
            == 11
        )

    matching_backup = root / "lifecycle-matching-backup.db"
    matching_revision = repository.backup(root, matching_backup)
    assert matching_revision == edited_revision
    accepted = config_editor.reset_managed_configuration(
        expected_revision=matching_revision
    )
    assert accepted.settings["default_api_timeout"].source == "default"
    with sqlite3.connect(root / "settings.db") as conn:
        assert repository.revision(conn) == matching_revision + 1
