"""Explorer operations exercise real mutations, search, selection and workspace state."""

import asyncio
import re

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_fullstack import hold_delivery, open_explorer

pytestmark = pytest.mark.scenario


async def test_explorer_selection_applicability_refresh_and_references(browser_app, ui):
    browser_app.app.write(browser_app.vault, "Notes/A.md", "alpha")
    browser_app.app.write(browser_app.vault, "Notes/B.md", "beta")
    explorer = await open_explorer(ui, browser_app)
    await expect(
        explorer.get_by_role("button", name="Rename", exact=True)
    ).to_have_count(0)
    await explorer.get_by_role("button", name="Expand Notes", exact=True).click()
    await explorer.get_by_role("checkbox", name="Select A.md", exact=True).check()
    await expect(
        explorer.get_by_role("button", name="Rename", exact=True)
    ).to_be_enabled()
    await explorer.get_by_role("checkbox", name="Select B.md", exact=True).check()
    await expect(
        explorer.get_by_role("button", name="Rename", exact=True)
    ).to_have_count(0)
    await expect(
        explorer.get_by_role("button", name="2 selected", exact=True)
    ).to_be_visible()
    await explorer.get_by_role("button", name="Refresh", exact=True).click()
    await expect(
        explorer.get_by_role("checkbox", name="Select A.md", exact=True)
    ).to_be_checked()
    await expect(
        explorer.get_by_role("checkbox", name="Select B.md", exact=True)
    ).to_be_checked()
    await explorer.get_by_role("button", name="Add to prompt", exact=True).click()
    draft = await ui.locator("#chat-input").input_value()
    assert "Notes/A.md" in draft and "Notes/B.md" in draft


async def test_explorer_create_rename_delete_and_duplicate_submit_lock(browser_app, ui):
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("button", name="Create or upload").click()
    await explorer.get_by_role("menuitem", name="New file", exact=True).click()
    await explorer.get_by_label("File name", exact=True).fill("Created.md")
    form = explorer.locator("[data-vault-explorer-mutation-form]")
    async with hold_delivery(
        ui,
        browser_app.app,
        browser_app.url + f"/api/vaults/{browser_app.vault.name}/paths/mutate",
        method="POST",
    ) as held:
        await form.get_by_role("button", name="Create", exact=True).click()
        await asyncio.wait_for(held.held.wait(), 10)
        await expect(
            form.get_by_role("button", name="Create", exact=True)
        ).to_be_disabled()
        await form.get_by_label("File name", exact=True).press("Enter")
        assert len(held.requests) == 1
        held.release.set()
    await expect(
        explorer.get_by_role("checkbox", name="Select Created.md", exact=True)
    ).to_be_visible()
    assert (browser_app.vault / "Created.md").exists()
    reader = ui.get_by_role("dialog").filter(has=ui.locator("#vault-file-modal-path"))
    await expect(reader).to_be_visible()
    await reader.get_by_role("button", name="Back", exact=True).click()
    await explorer.get_by_role("checkbox", name="Select Created.md", exact=True).check()
    await explorer.get_by_role("button", name="Rename", exact=True).click()
    await explorer.get_by_label("New name", exact=True).fill("Renamed.md")
    await form.get_by_role("button", name="Rename", exact=True).click()
    await expect(
        explorer.get_by_role("checkbox", name="Select Renamed.md", exact=True)
    ).to_be_visible()
    assert not (browser_app.vault / "Created.md").exists()
    await expect(
        explorer.get_by_role("checkbox", name="Select Renamed.md", exact=True)
    ).to_be_checked()
    await expect(
        explorer.get_by_role("button", name="1 selected", exact=True)
    ).to_be_visible()
    await explorer.get_by_role("button", name="Delete", exact=True).click()
    await form.get_by_role("button", name="Delete", exact=True).click()
    await expect(
        explorer.get_by_role("checkbox", name="Select Renamed.md", exact=True)
    ).to_have_count(0)
    assert not (browser_app.vault / "Renamed.md").exists()


async def test_explorer_content_search_escapes_untrusted_file_text(browser_app, ui):
    browser_app.app.write(
        browser_app.vault,
        "Needle.md",
        'Unsafe marker <img src=x onerror="window.browserInjection=1"> is literal text.',
    )
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("button", name="Search mode", exact=True).click()
    await explorer.get_by_role("menuitemradio", name="Contents", exact=True).click()
    await explorer.get_by_role("searchbox", name="Search files", exact=True).fill(
        "Unsafe marker"
    )
    await expect(explorer.get_by_text(re.compile("Unsafe marker"))).to_be_visible()
    assert await explorer.locator('img[src="x"]').count() == 0
    assert await ui.evaluate("typeof window.browserInjection") == "undefined"


async def test_explorer_workspace_save_updates_open_navigation(browser_app, ui):
    browser_app.app.write(browser_app.vault, "Project/Plan.md", "Workspace plan")
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("checkbox", name="Select Project", exact=True).check()
    await explorer.get_by_role("button", name="Set as workspace", exact=True).click()
    toggle = explorer.get_by_role("button", name="Workspace view", exact=True)
    await expect(toggle).to_be_enabled()
    await toggle.click()
    await expect(
        explorer.get_by_role("checkbox", name="Select Plan.md", exact=True)
    ).to_be_visible()
    await toggle.click()
    await expect(
        explorer.get_by_role("checkbox", name="Select Project", exact=True)
    ).to_be_visible()
