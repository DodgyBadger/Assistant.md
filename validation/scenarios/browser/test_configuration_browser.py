"""Settings/model edits and real-network interleavings, without in-app mocks."""

import asyncio
import json

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_fullstack import (
    hold_delivery,
    lose_connection,
    setting,
)
from validation.scenarios.support.browser_journeys import open_settings

pytestmark = pytest.mark.scenario


def entry(application, key):
    return next(
        s
        for s in application.app.request("/api/system/settings/general").json()
        if s["key"] == key
    )


async def models(ui, application):
    await ui.goto(application.url)
    await ui.get_by_role("button", name="System", exact=True).click()
    await ui.locator("#config-models-section > summary").click()
    return ui.locator("#model-list")


@pytest.mark.parametrize("value", ["", "-1", "33", "1.5"])
async def test_invalid_numeric_input_cannot_mutate_saved_setting(
    browser_app, ui, value
):
    key = "max_concurrent_delegates"
    baseline = entry(browser_app, key)["value"]
    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label(key, exact=True).fill(value)
    await card.get_by_role("button", name="Save setting", exact=True).click()
    await expect(ui.get_by_label(key, exact=True)).to_be_visible()
    assert entry(browser_app, key)["value"] == baseline
    await card.get_by_role("button", name="Cancel setting edit").click()
    await expect(card.get_by_text(str(baseline), exact=True)).to_be_visible()


async def test_ordered_pdf_strategies_reorder_remove_and_readd_durably(browser_app, ui):
    key = "ingestion_pdf_default_strategies"
    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await card.get_by_role("button", name="Move item 2 up", exact=True).click()
    await card.get_by_role("button", name="Save setting", exact=True).click()
    await expect(
        card.get_by_role("button", name="Edit setting", exact=True)
    ).to_be_visible()
    assert json.loads(entry(browser_app, key)["value"]) == ["pdf_text", "pdf_ocr"]
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await card.get_by_role("button", name="Remove item 2", exact=True).click()
    await card.get_by_role("button", name="Save setting", exact=True).click()
    await expect(
        card.get_by_role("button", name="Edit setting", exact=True)
    ).to_be_visible()
    assert json.loads(entry(browser_app, key)["value"]) == ["pdf_text"]
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await card.get_by_role("button", name="Add", exact=True).click()
    await card.get_by_role("button", name="Save setting", exact=True).click()
    await expect(
        card.get_by_role("button", name="Edit setting", exact=True)
    ).to_be_visible()
    assert json.loads(entry(browser_app, key)["value"]) == ["pdf_text", "pdf_ocr"]


async def test_failed_save_preserves_draft_and_retry_uses_real_service(browser_app, ui):
    key = "browser_max_calls_per_turn"
    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label(key, exact=True).fill("7")
    async with lose_connection(
        ui,
        browser_app.app,
        browser_app.url + f"/api/system/settings/general/{key}",
        method="PUT",
    ) as dropped:
        await card.get_by_role("button", name="Save setting", exact=True).click()
        await expect(ui.get_by_label(key, exact=True)).to_be_enabled()
        await expect(ui.locator("#settings-feedback")).not_to_be_empty()
        await expect(ui.get_by_label(key, exact=True)).to_have_value("7")
        assert entry(browser_app, key)["value"] == "4"
        assert len(dropped) == 1
    await card.get_by_role("button", name="Save setting", exact=True).click()
    await expect(
        card.get_by_role("button", name="Edit setting", exact=True)
    ).to_be_visible()
    assert entry(browser_app, key)["value"] == "7"


async def test_global_reset_cancels_a_pending_real_editor_read(browser_app, ui):
    key = "browser_max_calls_per_turn"
    card = await open_settings(ui, browser_app.url, key)
    async with hold_delivery(
        ui, browser_app.app, browser_app.url + "/api/system/settings/general"
    ) as gate:
        await card.get_by_role("button", name="Edit setting", exact=True).click()
        await asyncio.wait_for(gate.held.wait(), 10)
        ui.once("dialog", lambda dialog: dialog.accept())
        async with ui.expect_response(
            browser_app.url + "/api/system/settings/defaults"
        ):
            await ui.get_by_role("button", name="Use defaults", exact=True).click()
        gate.release.set()
    await expect(ui.get_by_label(key, exact=True)).to_have_count(0)
    await expect(
        card.get_by_role("button", name="Edit setting", exact=True)
    ).to_be_visible()
    assert entry(browser_app, key)["value"] == "4"


async def test_global_defaults_cancel_then_restore_discards_draft_and_updates_values(
    browser_app, ui
):
    key = "browser_max_calls_per_turn"
    setting(browser_app.app, key, "9")
    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label(key, exact=True).fill("7")
    ui.once("dialog", lambda dialog: dialog.dismiss())
    await ui.get_by_role("button", name="Use defaults", exact=True).click()
    await expect(ui.get_by_label(key, exact=True)).to_have_value("7")
    assert entry(browser_app, key)["value"] == "9"
    ui.once("dialog", lambda dialog: dialog.accept())
    await ui.get_by_role("button", name="Use defaults", exact=True).click()
    await expect(
        card.get_by_role("button", name="Edit setting", exact=True)
    ).to_be_visible()
    assert entry(browser_app, key)["value"] == "4"
    await expect(card.get_by_text("4", exact=True)).to_be_visible()


async def test_settings_unlock_before_slow_metadata_refresh(browser_app, ui):
    key = "browser_max_calls_per_turn"
    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label(key, exact=True).fill("7")
    async with hold_delivery(
        ui, browser_app.app, browser_app.url + "/api/metadata"
    ) as gate:
        await card.get_by_role("button", name="Save setting", exact=True).click()
        await asyncio.wait_for(gate.held.wait(), 10)
        await expect(
            card.get_by_role("button", name="Edit setting", exact=True)
        ).to_be_enabled()
        assert entry(browser_app, key)["value"] == "7"
        await card.get_by_role("button", name="Use default", exact=True).click()
        await expect(
            card.get_by_role("button", name="Use default", exact=True)
        ).to_have_count(0)
        assert entry(browser_app, key)["value"] == "4"
        gate.release.set()


async def test_managed_binding_is_protected_and_duplicate_is_independent(
    browser_app, ui
):
    listing = await models(ui, browser_app)
    managed = listing.locator('[data-row="sonnet"]')
    await managed.get_by_role("button", name="Edit model details").click()
    await expect(managed.get_by_label("Model Alias", exact=True)).to_have_attribute(
        "readonly", ""
    )
    await expect(managed.get_by_label("Model Identifier", exact=True)).to_be_disabled()
    await managed.get_by_label("Alternate aliases", exact=True).fill("browser-alias")
    await managed.get_by_role("button", name="Save model", exact=True).click()
    await expect(
        managed.get_by_role("button", name="Edit model details")
    ).to_be_visible()
    source = next(
        m
        for m in browser_app.app.request("/api/system/models").json()
        if m["name"] == "sonnet"
    )
    assert source["alternate_aliases"] == ["browser-alias"]
    await managed.get_by_role("button", name="Duplicate as custom").click()
    duplicate = listing.locator('[data-mode="edit"]')
    await expect(duplicate.get_by_label("Model Alias", exact=True)).to_be_editable()
    await expect(duplicate.get_by_label("Alternate aliases", exact=True)).to_have_value(
        ""
    )
    await duplicate.get_by_label("Model Alias", exact=True).fill("browser-copy")
    await duplicate.get_by_role("button", name="Save model", exact=True).click()
    custom = listing.locator('[data-row="browser-copy"]')
    await expect(custom.get_by_role("button", name="Delete model")).to_be_visible()
    ui.once("dialog", lambda dialog: dialog.accept())
    await custom.get_by_role("button", name="Delete model").click()
    await expect(custom).to_have_count(0)
    assert any(
        m["name"] == "sonnet"
        for m in browser_app.app.request("/api/system/models").json()
    )


async def test_custom_model_collision_preserves_draft_then_atomic_rename_succeeds(
    browser_app, ui
):
    response = browser_app.app.request(
        "/api/system/models/browser-custom",
        method="PUT",
        data={"provider": "test", "model_string": "test", "capabilities": ["text"]},
    )
    assert response.status_code == 200, response.text
    listing = await models(ui, browser_app)
    card = listing.locator('[data-row="browser-custom"]')
    await card.get_by_role("button", name="Edit model details").click()
    await card.get_by_label("Model Alias", exact=True).fill("sonnet")
    await card.get_by_role("button", name="Save model", exact=True).click()
    await expect(card.get_by_label("Model Alias", exact=True)).to_have_value("sonnet")
    assert any(
        m["name"] == "browser-custom"
        for m in browser_app.app.request("/api/system/models").json()
    )
    await card.get_by_label("Model Alias", exact=True).fill("browser-renamed")
    await card.get_by_role("button", name="Save model", exact=True).click()
    await expect(listing.locator('[data-row="browser-renamed"]')).to_be_visible()
    names = {m["name"] for m in browser_app.app.request("/api/system/models").json()}
    assert "browser-renamed" in names and "browser-custom" not in names
