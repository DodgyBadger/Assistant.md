"""Settings controls reflect mutation completion independently of slow refreshes."""

from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("operation", ["save", "reset"])
def test_controls_unlock_before_metadata_refresh_finishes(operation: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        page = browser.new_page()
        page.set_content(
            '<div id="feedback"></div><div id="validation"></div><div id="settings"></div>'
        )
        for module in ("icons", "configuration/runtime", "configuration/settings"):
            page.add_script_tag(path=str(_ROOT / f"static/js/{module}.js"))
        page.evaluate("""() => {
            const r = ConfigurationPanelRuntime;
            r.elements.settingsList = document.querySelector('#settings');
            r.elements.settingsFeedback = document.querySelector('#feedback');
            r.elements.settingsValidation = document.querySelector('#validation');
            r.actions.updateImportOcrAvailability = () => {};
            r.state.settings = ['first', 'other'].map(key => ({key, value:'9', default_value:'3',
                source:'custom', editor:{kind:'integer',minimum:0,maximum:32}, issues:[]}));
            window.entries = r.state.settings;
            r.elements.settingsList.addEventListener('click', r.actions.handleSettingsTableClick);
            r.callbacks.refreshMetadata = () => new Promise(resolve => { window.releaseMetadata = resolve; });
            window.fetch = async (url, options = {}) => {
                if (options.method === 'PUT' || options.method === 'DELETE') {
                    const result = {...entries[0], value:options.method === 'PUT' ? '7':'3',
                        source:options.method === 'PUT' ? 'custom':'default'};
                    entries = [result, entries[1]];
                    return {ok:true,json:async()=>result};
                }
                return {ok:true,json:async()=>entries};
            };
            r.actions.renderSettings();
        }""")
        if operation == "save":
            page.locator('[data-setting="first"] [data-action="edit-setting"]').click()
            page.locator('[data-field="setting-value"]').fill("7")
            page.locator('[data-action="save-setting"]').click()
        else:
            page.locator(
                '[data-setting="first"] [data-action="restore-setting-default"]'
            ).click()
        page.wait_for_function("typeof releaseMetadata === 'function'")
        expect(
            page.locator(
                '[data-setting="other"] [data-action="restore-setting-default"]'
            )
        ).to_be_enabled()
        assert page.evaluate("ConfigurationPanelRuntime.state.isSavingSetting") is False
        page.locator('[data-setting="other"] [data-action="edit-setting"]').click()
        expect(page.locator('[data-setting="other"][data-mode="edit"]')).to_be_visible()
        page.evaluate("releaseMetadata()")
        expect(page.locator('[data-setting="other"][data-mode="edit"]')).to_be_visible()
        browser.close()
