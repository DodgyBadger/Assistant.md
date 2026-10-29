"""Old managed identities cannot overwrite a custom current catalog alias."""

from __future__ import annotations

from copy import deepcopy

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.legacy_model_catalog import (
    import_legacy_catalog,
    legacy_catalogs,
)
from validation.scenarios.support.model_configuration import install_legacy_models

pytestmark = pytest.mark.scenario


async def test_real_catalog_upgrade_preserves_old_and_custom_collision(
    app: Application,
) -> None:
    await app.start()
    old = deepcopy(legacy_catalogs()["aliases"]["models"]["gpt"])
    old["managed_id"] = "builtin-gpt"
    custom = {
        "managed_id": None,
        "provider": "test",
        "model_string": "test",
        "capabilities": ["text"],
        "user_editable": True,
        "alternate_aliases": ["custom-sol-history"],
    }
    legacy = {"models": {"gpt": old, "sol": custom}, "providers": {}}
    imported = import_legacy_catalog(app, "collision", legacy)
    assert imported["backups"] == [imported["original"]]
    for name, expected in legacy["models"].items():
        for key, value in expected.items():
            assert imported["persisted_models"][name][key] == value
            assert imported["effective"]["models"][name][key] == value
    # The running application's actual configuration reload projects the same
    # conflict to health and activity, using its shipped catalog as the target.
    snapshot = app.configuration()
    snapshot["models"]["gpt"] = deepcopy(old)
    snapshot["models"]["sol"] = deepcopy(custom)
    install_legacy_models(app, snapshot)
    persisted = app.configuration()
    for name, expected in legacy["models"].items():
        for key, value in expected.items():
            assert persisted["models"][name][key] == value
    health = app.request("/api/status")
    assert health.status_code == 200
    issues = health.json()["configuration_status"]["issues"]
    assert any(
        issue["name"] == "models:catalog_update"
        and issue["severity"] == "warning"
        and "sol" in issue["message"]
        for issue in issues
    )
    activity = app.request("/api/system/activity-log", params={"limit": 1000})
    assert activity.status_code == 200
    blocked = [
        entry["data"]
        for entry in activity.json()["entries"]
        if entry.get("data", {}).get("event") == "managed_models_update_blocked"
    ]
    assert blocked and any(
        row["status"] == "blocked" and any("sol" in reason for reason in row["reasons"])
        for row in blocked
    )
