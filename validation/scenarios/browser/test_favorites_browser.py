"""Bookmarks through the real UI, API and persistent stores."""

import asyncio

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_fullstack import (
    hold_delivery,
    lose_connection,
    open_explorer,
)
from validation.support.browser.browser_sessions import session_list, thread

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("width", [1280, 390], ids=["desktop", "mobile"])
async def test_chat_bookmarks_resume_existing_history(browser_app, ui, width):
    await ui.set_viewport_size({"width": width, "height": 900})
    # Session IDs also preserve this order when activity timestamps share a second.
    store = thread(browser_app, "a-older", [("Older question", "Older answer")])
    thread(browser_app, "z-newer", [("Newer question", "Newer answer")])
    store.set_session_favorite("a-older", browser_app.vault.name, True)
    dialog = await session_list(ui, browser_app)
    await dialog.locator(
        '[data-session-action="favorite"][data-session-action-id="z-newer"]'
    ).click()
    await expect(
        dialog.locator(
            '[data-session-action="favorite"][data-session-action-id="z-newer"]'
        )
    ).to_have_attribute("aria-pressed", "true")
    assert await dialog.locator("[data-session-browser-row-id]").evaluate_all(
        "rows => rows.map(row => row.dataset.sessionBrowserRowId)"
    ) == ["z-newer", "a-older"]
    await dialog.locator(
        '[data-session-action="favorite"][data-session-action-id="a-older"]'
    ).click()
    await expect(
        dialog.locator(
            '[data-session-action="favorite"][data-session-action-id="a-older"]'
        )
    ).to_have_attribute("aria-pressed", "false")
    await dialog.get_by_role("button", name="Close chat settings").click()
    await expect(ui.locator("[data-chat-favorite]")).to_have_count(1)
    card = ui.locator('[data-chat-favorite="z-newer"]')
    await card.focus()
    await ui.keyboard.press("Enter")
    await expect(
        ui.locator("#chat-messages").get_by_text("Newer answer", exact=True)
    ).to_be_visible()
    assert (
        store.get_session_metadata("z-newer", browser_app.vault.name)["favorite"]
        is True
    )
    assert (
        store.get_session_metadata("a-older", browser_app.vault.name)["favorite"]
        is False
    )
    assert (
        len(
            browser_app.app.request(
                "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
            ).json()
        )
        == 2
    )


async def test_late_chat_bookmark_does_not_modify_another_vault(browser_app, ui):
    from core.identity import LOCAL_USER_PRINCIPAL_ID

    store = thread(browser_app, "source", [("Question", "Answer")])
    other = browser_app.app.vault("OtherFavoritesVault")
    store.ensure_session(
        "other-session", other.name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    response = await browser_app.app.request_async(
        "/api/vaults/rescan", method="POST", data={}
    )
    assert response.status_code == 200, response.text
    dialog = await session_list(ui, browser_app)
    async with hold_delivery(
        ui, browser_app.app, "**/api/chat/sessions/source/favorite", method="PATCH"
    ) as held:
        await dialog.locator('[data-session-action="favorite"]').click()
        await asyncio.wait_for(held.held.wait(), 10)
        await dialog.get_by_role("button", name="Close chat settings").click()
        await ui.locator("#vault-selector").select_option(other.name)
        await ui.get_by_role("button", name="Open chat settings", exact=True).click()
        await expect(
            ui.locator('[data-session-browser-row-id="other-session"]')
        ).to_be_visible()
        held.release.set()
    await expect(
        ui.locator(
            '[data-session-action="favorite"][data-session-action-id="other-session"]'
        )
    ).to_have_attribute("aria-pressed", "false")
    await ui.get_by_role("button", name="Close chat settings").click()
    await expect(ui.locator("[data-chat-favorite]")).to_have_count(0)
    assert (
        store.get_session_metadata("source", browser_app.vault.name)["favorite"] is True
    )
    other_sessions = browser_app.app.request(
        "/api/chat/sessions", params={"vault_name": other.name}
    ).json()
    assert (
        next(row for row in other_sessions if row["session_id"] == "other-session")[
            "favorite"
        ]
        is False
    )


@pytest.mark.parametrize("width", [1280, 390], ids=["desktop", "mobile"])
async def test_file_folder_bookmarks_filter_search_and_follow_reader_changes(
    browser_app, ui, width
):
    await ui.set_viewport_size({"width": width, "height": 900})
    for path in ("a.md", "b.md", "Folder/child.md"):
        browser_app.app.write(browser_app.vault, path, "bookmark needle")
    url = f"/api/vaults/{browser_app.vault.name}/favorites"
    assert (
        browser_app.app.request(
            url, "PATCH", {"paths": ["b.md"], "favorite": True}
        ).status_code
        == 200
    )
    explorer = await open_explorer(ui, browser_app)
    for path in ("Folder", "a.md", "b.md"):
        await explorer.locator(
            f'[data-vault-explorer-select-item][data-path="{path}"]'
        ).check()
    toggle = explorer.locator('[data-vault-explorer-toolbar-action="favorite"]')
    await expect(toggle).to_have_attribute("aria-pressed", "false")
    for expected, paths in (
        ("true", {"Folder", "a.md", "b.md"}),
        ("false", set()),
        ("true", {"Folder", "a.md", "b.md"}),
    ):
        await toggle.click()
        await expect(toggle).to_have_attribute("aria-pressed", expected)
        assert set(browser_app.app.request(url).json()["paths"]) == paths
    await explorer.locator("[data-vault-favorites-filter]").click()
    query = explorer.locator("[data-vault-path-picker-query]")
    await query.fill("a.md")
    await expect(
        explorer.locator(
            "[data-vault-path-picker-results] [data-vault-path-picker-row]"
        )
    ).to_have_count(1)
    await explorer.locator("[data-vault-explorer-search-mode-toggle]").click()
    await explorer.get_by_role("menuitemradio", name="Contents", exact=True).click()
    await query.fill("needle")
    await expect(
        explorer.locator(
            "[data-vault-path-picker-results] [data-vault-path-picker-row]"
        )
    ).to_have_count(2)
    await explorer.locator('[data-vault-path-picker-select="a.md"]').click()
    bookmark = ui.locator("[data-vault-file-favorite]")
    await expect(bookmark).to_have_attribute("aria-pressed", "true")
    await bookmark.click()
    await expect(bookmark).to_have_attribute("aria-pressed", "false")
    await ui.locator("[data-vault-file-back]").click()
    await expect(
        explorer.locator(
            "[data-vault-path-picker-results] [data-vault-path-picker-row]"
        )
    ).to_have_count(1)
    assert set(browser_app.app.request(url).json()["paths"]) == {"Folder", "b.md"}
    await explorer.locator("[data-vault-explorer-search-mode-toggle]").click()
    await explorer.get_by_role("menuitemradio", name="Files", exact=True).click()
    await query.fill("")
    await explorer.locator('[data-vault-path-picker-select="Folder"]').click()
    await expect(explorer.locator("[data-vault-favorites-filter]")).to_have_attribute(
        "aria-pressed", "false"
    )
    await expect(
        explorer.locator('[data-vault-path-picker-select="Folder/child.md"]')
    ).to_be_visible()


async def test_pending_and_disconnected_bookmark_keeps_selection_usable(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "a.md", "bookmark needle")
    explorer = await open_explorer(ui, browser_app)
    await explorer.locator(
        '[data-vault-explorer-select-item][data-path="a.md"]'
    ).check()
    toggle = explorer.locator('[data-vault-explorer-toolbar-action="favorite"]')
    pattern = f"**/api/vaults/{browser_app.vault.name}/favorites"
    async with hold_delivery(ui, browser_app.app, pattern, method="PATCH") as held:
        await toggle.click()
        await asyncio.wait_for(held.held.wait(), 10)
        await expect(toggle).to_be_visible()
        await expect(toggle).to_be_disabled()
        held.release.set()
    await expect(toggle).to_be_enabled()
    await expect(toggle).to_have_attribute("aria-pressed", "true")
    async with lose_connection(ui, browser_app.app, pattern, method="PATCH") as calls:
        await toggle.click()
        await expect(
            explorer.locator("[data-vault-path-picker-status]")
        ).to_contain_text("Failed to fetch")
        assert len(calls) == 1
    await expect(toggle).to_be_enabled()
    await expect(toggle).to_have_attribute("aria-pressed", "true")
    assert browser_app.app.request(
        f"/api/vaults/{browser_app.vault.name}/favorites"
    ).json()["paths"] == ["a.md"]
