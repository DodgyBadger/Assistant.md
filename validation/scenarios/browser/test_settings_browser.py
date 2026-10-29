"""A real user changes and restores settings, with durable reload verification."""

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_journeys import open_settings

pytestmark = pytest.mark.scenario


async def test_setting_save_reload_and_restore_default(browser_app, ui):
    key = "browser_max_calls_per_turn"
    settings = browser_app.app.request("/api/system/settings/general").json()
    default = next(s["default_value"] for s in settings if s["key"] == key)

    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label(key, exact=True).fill("7")
    await card.get_by_role("button", name="Save setting", exact=True).click()
    await expect(
        card.get_by_role("button", name="Use default", exact=True)
    ).to_be_visible()

    card = await open_settings(ui, browser_app.url, key)
    await expect(card.get_by_text("7", exact=True)).to_be_visible()
    persisted = browser_app.app.request("/api/system/settings/general").json()
    assert next(s["value"] for s in persisted if s["key"] == key) == "7"

    await card.get_by_role("button", name="Use default", exact=True).click()
    await expect(
        card.get_by_role("button", name="Use default", exact=True)
    ).to_have_count(0)
    card = await open_settings(ui, browser_app.url, key)
    await expect(card.get_by_text(str(default), exact=True)).to_be_visible()
    persisted = browser_app.app.request("/api/system/settings/general").json()
    entry = next(s for s in persisted if s["key"] == key)
    assert entry["value"] == str(default) and entry["source"] == "default"
    browser_app.app.evidence.save_json("settings-browser-result.json", entry)
