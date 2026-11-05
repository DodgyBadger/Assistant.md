"""Explorer operations exercise real mutations, search, selection and workspace state."""

import asyncio
import re

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_fullstack import hold_delivery, open_explorer

pytestmark = pytest.mark.scenario


async def test_explorer_selection_applicability_refresh_and_references(browser_app, ui):
    browser_app.app.write(browser_app.vault, "Notes/A.md", "alpha")
    browser_app.app.write(browser_app.vault, "Notes/B.md", "beta")
    for i in range(50):
        browser_app.app.write(
            browser_app.vault, f"Notes/Filler-{i:02}.md", "reading input"
        )
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
    await explorer.locator(
        '[data-vault-path-picker-select="Notes/Filler-49.md"]'
    ).scroll_into_view_if_needed()
    results = explorer.locator("[data-vault-path-picker-results]")
    scroll_before = await results.evaluate("element => element.scrollTop")
    assert scroll_before > 0
    await explorer.get_by_role("button", name="Refresh", exact=True).click()
    await expect(
        explorer.get_by_role("checkbox", name="Select A.md", exact=True)
    ).to_be_checked()
    await expect(
        explorer.get_by_role("checkbox", name="Select B.md", exact=True)
    ).to_be_checked()
    scroll_after = await results.evaluate("element => element.scrollTop")
    assert abs(scroll_after - scroll_before) <= 2
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


@pytest.mark.parametrize("width", [1280, 390], ids=["desktop", "mobile"])
async def test_explorer_workspace_save_updates_open_navigation(browser_app, ui, width):
    await ui.set_viewport_size({"width": width, "height": 900})
    browser_app.app.write(browser_app.vault, "Project/Plan.md", "Workspace plan")
    browser_app.app.write(browser_app.vault, "Project/Ordinary.md", "Ordinary file")
    browser_app.app.write(browser_app.vault, "Outside.md", "Outside favorite")
    response = browser_app.app.request(
        f"/api/vaults/{browser_app.vault.name}/favorites",
        "PATCH",
        {"paths": ["Project/Plan.md", "Outside.md"], "favorite": True},
    )
    assert response.status_code == 200, response.text
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("checkbox", name="Select Project", exact=True).check()
    await explorer.get_by_role("button", name="Set as workspace", exact=True).click()
    toggle = explorer.get_by_role("button", name="Workspace view", exact=True)
    await expect(toggle).to_be_enabled()
    await toggle.click()
    await expect(
        explorer.get_by_role("checkbox", name="Select Plan.md", exact=True)
    ).to_be_visible()
    favorites = explorer.locator("[data-vault-favorites-filter]")
    await favorites.click()
    await expect(favorites).to_have_attribute("aria-pressed", "true")
    await expect(
        explorer.locator('[data-vault-path-picker-select="Project/Plan.md"]')
    ).to_be_visible()
    await expect(
        explorer.locator('[data-vault-path-picker-select="Project/Ordinary.md"]')
    ).to_have_count(0)
    await expect(
        explorer.locator('[data-vault-path-picker-select="Outside.md"]')
    ).to_have_count(0)
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-pressed", "false")
    await expect(favorites).to_have_attribute("aria-pressed", "true")
    await expect(
        explorer.locator('[data-vault-path-picker-select="Outside.md"]')
    ).to_be_visible()
    await toggle.click()
    await expect(toggle).to_have_attribute("aria-pressed", "true")
    await expect(favorites).to_have_attribute("aria-pressed", "true")
    await expect(
        explorer.locator('[data-vault-path-picker-select="Outside.md"]')
    ).to_have_count(0)
    await expect(
        explorer.locator('[data-vault-path-picker-select="Project/Plan.md"]')
    ).to_be_visible()
    await favorites.click()
    await expect(favorites).to_have_attribute("aria-pressed", "false")
    await expect(toggle).to_have_attribute("aria-pressed", "true")
    await expect(
        explorer.locator('[data-vault-path-picker-select="Project/Ordinary.md"]')
    ).to_be_visible()


async def test_batch_move_keeps_contents_and_rejects_descendant_destination(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "Source/Child/A.md", "first content")
    browser_app.app.write(browser_app.vault, "B.md", "second content")
    browser_app.app.write(browser_app.vault, "Target/Existing.md", "existing content")
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("button", name="Expand all folders").click()
    await explorer.get_by_role("checkbox", name="Select Source", exact=True).check()
    await explorer.get_by_role("checkbox", name="Select B.md", exact=True).check()
    await explorer.get_by_role("button", name="Move", exact=True).click()
    await explorer.locator('[data-vault-path-picker-select="Source/Child"]').click()
    await expect(explorer).to_contain_text("cannot be moved into itself")
    assert (browser_app.vault / "Source/Child/A.md").read_text() == "first content"
    await explorer.locator('[data-vault-path-picker-select="Target"]').click()
    await explorer.get_by_role("button", name="Move here", exact=True).click()
    await expect(
        explorer.get_by_role("checkbox", name="Select B.md", exact=True)
    ).to_be_visible()
    assert not (browser_app.vault / "Source").exists()
    assert not (browser_app.vault / "B.md").exists()
    assert (
        browser_app.vault / "Target/Source/Child/A.md"
    ).read_text() == "first content"
    assert (browser_app.vault / "Target/B.md").read_text() == "second content"
    assert (browser_app.vault / "Target/Existing.md").read_text() == "existing content"


async def test_bulk_delete_across_folders_requires_confirmation_and_can_cancel(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "First/A.md", "alpha")
    browser_app.app.write(browser_app.vault, "Second/B.md", "beta")
    browser_app.app.write(browser_app.vault, "Second/Keep.md", "retain")
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("button", name="Expand First", exact=True).click()
    await explorer.get_by_role("button", name="Expand Second", exact=True).click()
    await explorer.get_by_role("checkbox", name="Select A.md", exact=True).check()
    await explorer.get_by_role("checkbox", name="Select B.md", exact=True).check()
    await explorer.get_by_role("button", name="Delete", exact=True).click()
    form = explorer.locator(
        '[data-vault-explorer-mutation-form][data-operation="delete"]'
    )
    await expect(form).to_be_visible()
    assert (browser_app.vault / "First/A.md").exists()
    assert (browser_app.vault / "Second/B.md").exists()
    await form.get_by_role("button", name="Cancel", exact=True).click()
    assert (browser_app.vault / "First/A.md").exists()
    assert (browser_app.vault / "Second/B.md").exists()
    await explorer.get_by_role("button", name="Delete", exact=True).click()
    await form.get_by_role("button", name="Delete", exact=True).click()
    await expect(
        explorer.get_by_role("checkbox", name="Select A.md", exact=True)
    ).to_have_count(0)
    await expect(
        explorer.get_by_role("checkbox", name="Select B.md", exact=True)
    ).to_have_count(0)
    assert not (browser_app.vault / "First/A.md").exists()
    assert not (browser_app.vault / "Second/B.md").exists()
    assert (browser_app.vault / "Second/Keep.md").read_text() == "retain"
    await explorer.get_by_role("checkbox", name="Select Second", exact=True).check()
    await explorer.get_by_role("button", name="Delete", exact=True).click()
    assert (browser_app.vault / "Second/Keep.md").exists()
    async with ui.expect_response(
        lambda response: "/paths/delete-batch" in response.url
    ) as deletion:
        await form.get_by_role("button", name="Delete", exact=True).click()
    activity_id = (await (await deletion.value).json())["activity_id"]
    await expect(
        explorer.get_by_role("checkbox", name="Select Second", exact=True)
    ).to_have_count(0)
    assert not (browser_app.vault / "Second").exists()
    await explorer.get_by_role("button", name="Close", exact=True).click()
    await ui.locator("#dashboard-tab").click()
    await ui.locator("#dashboard-vault-activity-section > summary").click()
    await ui.get_by_role("button", name="Refresh Activity", exact=True).click()
    await ui.locator(f'[data-vault-activity-id="{activity_id}"]').click()
    activity = ui.locator("#vault-activity-modal")
    ui.once("dialog", lambda dialog: dialog.accept())
    await activity.get_by_role("button", name="Rollback activity", exact=True).click()
    await expect(activity).to_have_count(0)
    assert (browser_app.vault / "Second/Keep.md").read_text() == "retain"


@pytest.mark.parametrize("action", ["expand", "create"])
async def test_explorer_controls_handle_pending_initial_tree(browser_app, ui, action):
    browser_app.app.write(browser_app.vault, "Notes/Ready.md", "Ready to read")
    async with hold_delivery(
        ui,
        browser_app.app,
        browser_app.url + f"/api/vaults/{browser_app.vault.name}/file-refs?scope=vault",
        first_only=True,
    ) as held:
        explorer = await open_explorer(ui, browser_app)
        await asyncio.wait_for(held.held.wait(), 10)
        expand = explorer.get_by_role("button", name="Expand all folders")
        if action == "expand":
            await expect(expand).to_be_disabled()
        else:
            await explorer.get_by_role("button", name="Create or upload").click()
            await expect(
                explorer.get_by_role("menuitem", name="New file", exact=True)
            ).to_be_visible()
        held.release.set()
    await expect(
        explorer.get_by_role("button", name="Expand Notes", exact=True)
    ).to_be_visible()
    if action == "expand":
        await expect(expand).to_be_enabled()
        await expand.click()
        await explorer.locator(
            '[data-vault-path-picker-select="Notes/Ready.md"]'
        ).click()
        await expect(ui.locator("#vault-file-modal-preview")).to_contain_text(
            "Ready to read"
        )
    else:
        await explorer.get_by_role("menuitem", name="New file", exact=True).click()
        await explorer.get_by_label("File name", exact=True).fill("Ready.md")
        await explorer.locator("[data-vault-explorer-mutation-form]").get_by_role(
            "button", name="Create", exact=True
        ).click()
        await expect(ui.locator("#vault-file-modal-path")).to_have_text("Ready.md")
        assert (browser_app.vault / "Ready.md").is_file()
