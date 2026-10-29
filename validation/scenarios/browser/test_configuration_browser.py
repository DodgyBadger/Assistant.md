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
        await expect(ui.locator("#settings-feedback")).to_contain_text("Failed to save")
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
    # Another configuration writer creates a collision after this editor's
    # catalog read. The backend must reject the stale rename atomically.
    await card.get_by_label("Model Alias", exact=True).fill("browser-collision")
    response = browser_app.app.request(
        "/api/system/models/browser-collision",
        method="PUT",
        data={
            "provider": "test",
            "model_string": "test",
            "capabilities": ["text"],
        },
    )
    assert response.status_code == 200, response.text
    async with hold_delivery(
        ui,
        browser_app.app,
        browser_app.url + "/api/system/models/browser-collision",
        method="PUT",
    ) as gate:
        await card.get_by_role("button", name="Save model", exact=True).click()
        await asyncio.wait_for(gate.held.wait(), 10)
        await expect(card.get_by_label("Model Alias", exact=True)).not_to_be_editable()
        await expect(
            card.get_by_role("button", name="Cancel model edit", exact=True)
        ).to_be_disabled()
        assert 400 <= gate.requests[0]["status"] < 500
        gate.release.set()
    await expect(card.get_by_label("Model Alias", exact=True)).to_have_value(
        "browser-collision"
    )
    assert any(
        m["name"] == "browser-custom"
        for m in browser_app.app.request("/api/system/models").json()
    )
    await card.get_by_label("Model Alias", exact=True).fill("browser-renamed")
    await card.get_by_role("button", name="Save model", exact=True).click()
    await expect(listing.locator('[data-row="browser-renamed"]')).to_be_visible()
    names = {m["name"] for m in browser_app.app.request("/api/system/models").json()}
    assert "browser-renamed" in names and "browser-custom" not in names


async def test_failed_global_reset_retains_draft_and_saved_configuration(
    browser_app, ui
):
    key = "browser_max_calls_per_turn"
    setting(browser_app.app, key, "9")
    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label(key, exact=True).fill("7")
    async with lose_connection(
        ui,
        browser_app.app,
        browser_app.url + "/api/system/settings/defaults",
        method="POST",
    ) as dropped:
        ui.once("dialog", lambda dialog: dialog.accept())
        await ui.get_by_role("button", name="Use defaults", exact=True).click()
        await expect(ui.locator("#settings-feedback")).to_contain_text("Failed")
        await expect(ui.get_by_label(key, exact=True)).to_have_value("7")
        assert entry(browser_app, key)["value"] == "9"
        assert len(dropped) == 1


async def test_restoring_one_default_preserves_another_unsaved_setting(browser_app, ui):
    key = "browser_max_calls_per_turn"
    setting(browser_app.app, key, "9")
    await open_settings(ui, browser_app.url, key)
    await ui.get_by_placeholder("Filter settings…").fill("")
    other = ui.locator('[data-setting="max_concurrent_delegates"]')
    await other.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label("max_concurrent_delegates", exact=True).fill("7")
    await ui.locator(f'[data-setting="{key}"]').get_by_role(
        "button", name="Use default", exact=True
    ).click()
    await expect(ui.get_by_label("max_concurrent_delegates", exact=True)).to_have_value(
        "7"
    )
    assert entry(browser_app, key)["value"] == "4"
    assert entry(browser_app, "max_concurrent_delegates")["value"] != "7"


async def test_old_metadata_completion_cannot_unlock_newer_pending_mutation(
    browser_app, ui
):
    from validation.scenarios.support.browser_fullstack import hold_deliveries

    key = "browser_max_calls_per_turn"
    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label(key, exact=True).fill("7")
    async with hold_deliveries(
        ui,
        browser_app.app,
        (browser_app.url + "/api/metadata", "GET"),
        (browser_app.url + f"/api/system/settings/general/{key}/override", "DELETE"),
    ) as (metadata, restore):
        await card.get_by_role("button", name="Save setting", exact=True).click()
        await asyncio.wait_for(metadata.held.wait(), 10)
        await card.get_by_role("button", name="Use default", exact=True).click()
        await asyncio.wait_for(restore.held.wait(), 10)
        metadata.release.set()
        await expect(
            card.get_by_role("button", name="Use default", exact=True)
        ).to_be_disabled()
        restore.release.set()
    await expect(
        card.get_by_role("button", name="Use default", exact=True)
    ).to_have_count(0)
    assert entry(browser_app, key)["value"] == "4"


@pytest.mark.parametrize(
    "key,value",
    [
        ("browser_max_calls_per_turn", "not-a-number"),
        ("default_model", "missing-browser-model"),
    ],
)
async def test_invalid_saved_value_remains_visible_until_explicitly_repaired(
    browser_app, ui, key, value
):
    from validation.scenarios.support.session_map_readiness import (
        set_invalid_readiness_setting,
    )

    # Native persisted configuration input represents a missing provider or
    # legacy invalid value. Validation APIs and editor code remain real.
    set_invalid_readiness_setting(browser_app.app, key, value)
    card = await open_settings(ui, browser_app.url, key)
    await expect(ui.locator("#settings-validation")).not_to_be_empty()
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await expect(card.locator("code")).to_have_text(value)
    await ui.get_by_placeholder("Filter settings…").fill("max_concurrent_delegates")
    await expect(ui.locator("#settings-validation")).not_to_be_empty()
    await ui.get_by_placeholder("Filter settings…").fill(key)
    await expect(card.locator("code")).to_have_text(value)
    assert entry(browser_app, key)["value"] == value


async def test_failed_validation_refresh_retains_saved_warning_and_reports_unavailability(
    browser_app, ui
):
    from validation.scenarios.support.session_map_readiness import (
        set_invalid_readiness_setting,
    )

    key = "default_model"
    set_invalid_readiness_setting(browser_app.app, key, "missing-browser-model")
    card = await open_settings(ui, browser_app.url, key)
    async with lose_connection(
        ui, browser_app.app, browser_app.url + "/api/system/settings/general"
    ) as dropped:
        await card.get_by_role("button", name="Edit setting", exact=True).click()
        await expect(ui.locator("#settings-validation")).to_contain_text(
            "validation is unavailable"
        )
        await expect(card.locator("code")).to_have_text("missing-browser-model")
        assert dropped
    assert entry(browser_app, key)["value"] == "missing-browser-model"


async def test_global_defaults_finish_after_older_reads_with_fresh_values(
    browser_app, ui
):
    key = "browser_max_calls_per_turn"
    setting(browser_app.app, key, "9")
    card = await open_settings(ui, browser_app.url, key)
    async with hold_delivery(
        ui, browser_app.app, browser_app.url + "/api/metadata"
    ) as gate:
        await card.get_by_role("button", name="Use default", exact=True).click()
        await asyncio.wait_for(gate.held.wait(), 10)
        # Restore defaults remains usable while the previous metadata refresh is
        # waiting. The older read cannot undo the reset or its completion notice.
        ui.once("dialog", lambda dialog: dialog.accept())
        await ui.get_by_role("button", name="Use defaults", exact=True).click()
        await expect(card.get_by_text("4", exact=True)).to_be_visible()
        gate.release.set()
    await expect(card.get_by_text("4", exact=True)).to_be_visible()
    await expect(ui.locator("#settings-feedback")).to_contain_text(
        "default", ignore_case=True
    )
    assert entry(browser_app, key)["source"] == "default"


async def test_global_defaults_discard_setting_model_and_provider_drafts(
    browser_app, ui
):
    response = browser_app.app.request(
        "/api/system/providers/browser-provider",
        method="PUT",
        data={"api_key": "BROWSER_PROVIDER_KEY"},
    )
    assert response.status_code == 200, response.text
    key = "browser_max_calls_per_turn"
    card = await open_settings(ui, browser_app.url, key)
    await card.get_by_role("button", name="Edit setting", exact=True).click()
    await ui.get_by_label(key, exact=True).fill("7")
    await ui.locator("#config-models-section > summary").click()
    model = ui.locator('[data-row="sonnet"]')
    await model.get_by_role("button", name="Edit model details", exact=True).click()
    await model.get_by_label("Alternate aliases", exact=True).fill(
        "unsaved-browser-alias"
    )
    await ui.locator("#config-providers-section > summary").click()
    await expect(
        ui.locator("#provider-list").get_by_text("OpenAI OAuth", exact=True)
    ).to_be_visible()
    await ui.locator('[data-provider="browser-provider"][data-action="edit"]').click()
    provider = ui.locator('[data-provider-row="browser-provider"]')
    await provider.locator('[data-provider-field="api_key"]').fill(
        "UNSAVED_BROWSER_SECRET_NAME"
    )
    ui.once("dialog", lambda dialog: dialog.accept())
    await ui.get_by_role("button", name="Use defaults", exact=True).click()
    await expect(
        card.get_by_role("button", name="Edit setting", exact=True)
    ).to_be_visible()
    await expect(ui.get_by_role("button", name="Save model", exact=True)).to_have_count(
        0
    )
    await expect(
        ui.get_by_role("button", name="Save provider", exact=True)
    ).to_have_count(0)
    assert entry(browser_app, key)["value"] == "4"
    assert "unsaved-browser-alias" not in str(
        browser_app.app.request("/api/system/models").json()
    )
    assert "UNSAVED_BROWSER_SECRET_NAME" not in str(
        browser_app.app.request("/api/system/providers").json()
    )


async def test_failed_save_refreshes_model_choices_without_discarding_draft(
    browser_app, ui
):
    from validation.scenarios.support.chat_idle_provider import idle_provider
    from validation.scenarios.support.session_ops_chat import configure_provider

    key = "default_model"
    baseline = entry(browser_app, key)["value"]
    # A controlled external endpoint supplies availability, without issuing any
    # model call. Model registration, deletion and settings validation are real.
    with idle_provider() as provider:
        configure_provider(browser_app.app, provider)
        card = await open_settings(ui, browser_app.url, key)
        await card.get_by_role("button", name="Edit setting", exact=True).click()
        await ui.get_by_label(key, exact=True).select_option("session-ops-model")
        response = browser_app.app.request(
            "/api/system/models/session-ops-model", method="DELETE"
        )
        assert response.status_code == 200, response.text
        await card.get_by_role("button", name="Save setting", exact=True).click()
        await expect(ui.locator("#settings-feedback")).to_contain_text("Failed to save")
        await expect(ui.get_by_label(key, exact=True)).to_have_value(
            "session-ops-model"
        )
        await expect(
            ui.get_by_label(key, exact=True).locator("option:checked")
        ).to_contain_text("unavailable")
        assert entry(browser_app, key)["value"] == baseline
        assert not provider.requests


async def test_global_reset_waits_for_fresh_model_and_provider_reads(browser_app, ui):
    from validation.scenarios.support.browser_fullstack import hold_deliveries

    # Native configuration input represents a saved nondefault managed provider;
    # all reset, loading and rendering implementations remain the real app.
    configuration = browser_app.app.configuration()
    configuration["providers"]["openai"]["auth_mode"] = "oauth"
    configuration["models"]["sonnet"]["model_string"] = "saved-before-reset-model"
    browser_app.app.install_configuration(configuration)
    async with hold_deliveries(
        ui,
        browser_app.app,
        (browser_app.url + "/api/system/models", "GET"),
        (browser_app.url + "/api/system/providers", "GET"),
    ) as (models_read, providers_read):
        await open_settings(ui, browser_app.url, "browser_max_calls_per_turn")
        await ui.locator("#config-models-section > summary").click()
        await ui.locator("#config-providers-section > summary").click()
        await asyncio.wait_for(models_read.held.wait(), 10)
        await asyncio.wait_for(providers_read.held.wait(), 10)
        ui.once("dialog", lambda dialog: dialog.accept())
        async with ui.expect_response(
            browser_app.url + "/api/system/settings/defaults"
        ):
            await ui.get_by_role("button", name="Use defaults", exact=True).click()
        assert "saved-before-reset-model" not in str(
            browser_app.app.request("/api/system/models").json()
        )
        models_read.release.set()
        providers_read.release.set()
        await expect(
            ui.locator("#model-list").get_by_text(
                "saved-before-reset-model", exact=False
            )
        ).to_have_count(0)
        await expect(
            ui.locator("#provider-list").get_by_text("Selected OAuth", exact=True)
        ).to_have_count(0)
        await expect(
            ui.locator("#provider-list").get_by_text("OpenAI OAuth", exact=True)
        ).to_be_visible()
    assert "saved-before-reset-model" not in str(
        browser_app.app.request("/api/system/models").json()
    )
    provider = next(
        row
        for row in browser_app.app.request("/api/system/providers").json()
        if row["name"] == "openai"
    )
    assert provider["configured_auth_mode"] == "api_key"
