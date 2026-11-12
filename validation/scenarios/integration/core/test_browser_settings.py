"""The browser inactivity policy is configurable through normal settings."""

from __future__ import annotations

import pytest

from validation.support.application import Application
from validation.support.settings.settings import general_settings

pytestmark = pytest.mark.scenario


async def test_browser_idle_timeout_defaults_override_rejection_and_restore(
    app: Application,
) -> None:
    await app.start()
    key = "browser_idle_timeout_seconds"
    defaults = general_settings(app)
    assert key in defaults, "Browser inactivity must be configurable in settings"
    # The app fixture seeds all shipped values as explicit overrides. Select
    # the real default through the production API before checking inheritance.
    selected_default = app.request(
        f"/api/system/settings/general/{key}/override", "DELETE"
    )
    assert selected_default.status_code == 200, selected_default.text
    setting = general_settings(app)[key]
    assert int(setting["value"]) == 600
    assert setting["source"] == "default"
    assert setting["restart_required"] is False

    saved = app.request(f"/api/system/settings/general/{key}", "PUT", {"value": "900"})
    assert saved.status_code == 200, saved.text
    assert int(general_settings(app)[key]["value"]) == 900
    assert app.configuration()["settings"][key]["value"] == 900
    assert general_settings(app)[key]["source"] == "custom"
    await app.controller.restart_system()
    assert int(general_settings(app)[key]["value"]) == 900
    assert general_settings(app)[key]["source"] == "custom"

    for value in ("0", "-1", "1.5", "NaN", "Infinity", "86401"):
        before = app.configuration()
        refused = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": value}
        )
        assert refused.status_code == 400, refused.text
        assert app.configuration() == before

    restored = app.request(f"/api/system/settings/general/{key}/override", "DELETE")
    assert restored.status_code == 200, restored.text
    assert int(general_settings(app)[key]["value"]) == 600
    assert general_settings(app)[key]["source"] == "default"
    assert key not in app.configuration()["settings"]
