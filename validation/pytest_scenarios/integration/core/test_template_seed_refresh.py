"""Explicit settings repair and seed refresh preserve user-owned choices."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.settings import (
    get_max_concurrent_delegates,
    get_model_stream_idle_timeout_seconds,
)
from core.settings.store import get_general_settings
from validation.pytest_scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_settings_repair_and_explicit_template_refresh(app: Application) -> None:
    system_root = app.root / "system"
    target = system_root / "Authoring" / "default.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("STALE GENERATED DEFAULT")
    workflow_target = target.parent / "nightly-user-notes-compaction.md"
    workflow_target.write_text(
        '---\nrun_type: workflow\nschedule: "cron: 0 2 * * *"\nenabled: true\ndescription: stale enabled workflow\n---\n\nSTALE WORKFLOW BODY\n'
    )
    expected = Path("core/authoring/seed_templates/context/default.md").read_text()
    expected_workflow = (
        Path("core/authoring/seed_templates/workflows/nightly-user-notes-compaction.md")
        .read_text()
        .replace("enabled: false", "enabled: true", 1)
    )
    await app.start()

    raw = app.configuration()
    raw["providers"]["openrouter"].pop("provider", None)
    raw["settings"].pop("openrouter_ignored_providers", None)
    raw["settings"].pop("max_concurrent_delegates", None)
    raw["settings"]["default_model"].pop("category", None)
    raw["settings"]["default_model"]["value"] = "haiku"
    raw["settings"]["delegate_tool_calls_limit"] = {
        "value": 32,
        "description": "stale removed setting",
        "category": "Delegation",
        "restart_required": False,
    }
    app.install_configuration(raw)
    assert get_max_concurrent_delegates() == 3
    assert get_model_stream_idle_timeout_seconds() == 120.0
    status = app.request("/api/status")
    assert status.status_code == 200, status.text
    assert all(
        issue.get("name") != "settings:missing_metadata"
        for issue in status.json().get("configuration_status", {}).get("issues", [])
    )

    repaired = app.request("/api/system/settings/repair", method="POST")
    assert repaired.status_code == 200, repaired.text
    current = app.configuration()
    assert "openrouter_ignored_providers" not in current["settings"]
    effective = {
        name: entry.model_dump(mode="json")
        for name, entry in get_general_settings().items()
    }
    activity = app.request("/api/system/activity-log?limit=200").json()
    repair_events = [
        entry["data"]
        for entry in activity["entries"]
        if entry.get("data", {}).get("event") == "settings_repair_completed"
    ]
    assert len(repair_events) == 1
    assert repair_events[0]["status"] == "completed"
    assert Path(repair_events[0]["backup_path"]).is_file()
    assert Path(repair_events[0]["database_path"]).is_file()
    assert current["providers"]["openrouter"].get("provider") == {
        "require_parameters": True
    }
    assert effective["openrouter_ignored_providers"]["value"] == ["azure"]
    assert effective["default_model"]["category"] == "Models"
    assert effective["default_model"]["value"] == "haiku"
    assert "delegate_tool_calls_limit" not in effective
    assert effective["max_concurrent_delegates"]["value"] == 3
    assert effective["model_stream_idle_timeout_seconds"]["value"] == 120.0

    for value in (-1, 33, "invalid"):
        persisted = app.configuration()
        persisted["settings"]["max_concurrent_delegates"] = {
            "value": value,
            "source": "custom",
        }
        app.install_configuration(persisted)
        assert get_max_concurrent_delegates() == 3
    persisted["settings"]["max_concurrent_delegates"]["value"] = 3
    app.install_configuration(persisted)
    unlimited = app.request(
        "/api/system/settings/general/max_concurrent_delegates",
        method="PUT",
        data={"value": "0"},
    )
    assert unlimited.status_code == 200, unlimited.text
    assert get_max_concurrent_delegates() == 0
    assert (
        app.request(
            "/api/system/settings/general/max_concurrent_delegates",
            method="PUT",
            data={"value": "-1"},
        ).status_code
        == 400
    )
    disabled = app.request(
        "/api/system/settings/general/model_stream_idle_timeout_seconds",
        method="PUT",
        data={"value": "0"},
    )
    assert disabled.status_code == 200, disabled.text
    assert get_model_stream_idle_timeout_seconds() == 0.0
    assert (
        app.request(
            "/api/system/settings/general/model_stream_idle_timeout_seconds",
            method="PUT",
            data={"value": "3601"},
        ).status_code
        == 400
    )

    assert target.read_text() == "STALE GENERATED DEFAULT"
    assert "STALE WORKFLOW BODY" in workflow_target.read_text()
    refreshed = app.request("/api/system/authoring/seed-refresh", method="POST")
    assert refreshed.status_code == 200, refreshed.text
    payload = refreshed.json()
    assert target.as_posix() in payload.get("updated", [])
    assert workflow_target.as_posix() in payload.get("updated", [])
    # Seed copies are machine-managed artifacts; exact packaged bytes are the refresh contract.
    assert target.read_text() == expected
    assert workflow_target.read_text() == expected_workflow
