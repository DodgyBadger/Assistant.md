"""The desktop dock permits concurrent chat/file use and retains layout preferences."""

import asyncio

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_fullstack import open_explorer, setting
from validation.support.browser.browser_sessions import (
    load_thread,
    session_list,
    thread,
)
from validation.support.chat.chat_idle_provider import idle_provider
from validation.support.chat.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_dock_resize_and_mode_changes_preserve_editor_and_reopen(browser_app, ui):
    browser_app.app.write(browser_app.vault, "Draft.md", "Initial file")
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("button", name="Use split view", exact=True).click()
    divider = ui.get_by_role("separator", name="Resize explorer", exact=True)
    await expect(divider).to_be_visible()
    await ui.locator("#chat-input").click()
    await ui.locator("#chat-input").fill("Keep this chat draft")
    before = await explorer.bounding_box()
    handle = await divider.bounding_box()
    await ui.mouse.move(handle["x"] + handle["width"] / 2, handle["y"] + 180)
    await ui.mouse.down()
    await ui.mouse.move(handle["x"] - 120, handle["y"] + 180)
    await ui.mouse.up()
    after = await explorer.bounding_box()
    # Geometry here is the functional user-controlled split, not a styling oracle.
    assert after["width"] > before["width"] + 90
    chat = await ui.locator("#chat-input").bounding_box()
    assert chat["x"] + chat["width"] <= after["x"]
    await explorer.locator('[data-vault-path-picker-select="Draft.md"]').click()
    reader = ui.get_by_role("dialog").filter(has=ui.locator("#vault-file-modal-path"))
    await reader.get_by_role("button", name="Edit Markdown", exact=True).click()
    editor = reader.locator("#vault-file-modal-editor")
    await editor.fill("Unsaved file changes")
    for tab, content in (
        ("dashboard", "dashboard"),
        ("configuration", "configuration"),
        ("chat", "chat"),
    ):
        await ui.locator(f"#{tab}-tab").click()
        await expect(ui.locator(f"#{content}-content")).to_be_visible()
        await expect(editor).to_be_editable()
        await expect(editor).to_have_value("Unsaved file changes")
        await expect(divider).to_be_visible()
    await reader.get_by_role("button", name="Use overlay view", exact=True).click()
    await reader.get_by_role("button", name="Use split view", exact=True).click()
    await expect(editor).to_have_value("Unsaved file changes")
    await ui.locator("#chat-input").click()
    await expect(ui.locator("#chat-input")).to_have_value("Keep this chat draft")
    await reader.get_by_role("button", name="Save file", exact=True).click()
    await expect(
        reader.locator("#vault-file-modal-preview").get_by_text(
            "Unsaved file changes", exact=True
        )
    ).to_be_visible()
    assert (browser_app.vault / "Draft.md").read_text() == "Unsaved file changes"
    await ui.locator("#chat-input").focus()
    await ui.keyboard.press("Escape")
    await expect(divider).to_be_visible()
    await reader.get_by_role("button", name="Close", exact=True).focus()
    await ui.keyboard.press("Escape")
    await expect(reader).to_have_count(0)
    explorer = await open_explorer(ui, browser_app)
    await expect(divider).to_be_visible()
    assert abs((await explorer.bounding_box())["width"] - after["width"]) <= 2


async def test_dock_preference_survives_reload_narrow_windows_and_focus_mode(
    browser_app, ui
):
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("button", name="Use split view", exact=True).click()
    divider = ui.get_by_role("separator", name="Resize explorer", exact=True)
    await divider.focus()
    before = (await explorer.bounding_box())["width"]
    await ui.keyboard.press("ArrowLeft")
    width = (await explorer.bounding_box())["width"]
    assert width > before
    await explorer.get_by_role("button", name="Close", exact=True).click()
    await ui.reload()
    explorer = await open_explorer(ui, browser_app)
    await expect(divider).to_be_visible()
    assert abs((await explorer.bounding_box())["width"] - width) <= 2
    await ui.set_viewport_size({"width": 390, "height": 900})
    await expect(explorer).to_have_attribute("aria-modal", "true")
    await expect(divider).to_be_hidden()
    await expect(
        explorer.get_by_role("button", name="Use split view", exact=True)
    ).to_have_count(0)
    await expect(
        explorer.get_by_role("button", name="Use overlay view", exact=True)
    ).to_have_count(0)
    await ui.set_viewport_size({"width": 1440, "height": 1000})
    await expect(divider).to_be_visible()
    assert abs((await explorer.bounding_box())["width"] - width) <= 2
    await ui.locator("#chat-focus-toggle-inline").click()
    await ui.locator("#chat-input").click()
    await ui.locator("#chat-input").fill("Working beside the explorer")
    await explorer.get_by_role("searchbox", name="Search files", exact=True).fill(
        "Welcome"
    )
    await expect(
        explorer.locator('[data-vault-path-picker-select="Welcome.md"]')
    ).to_be_visible()
    chat = await ui.locator("#chat-input").bounding_box()
    assert chat["x"] + chat["width"] <= (await explorer.bounding_box())["x"]


async def test_docked_file_keeps_run_locks_and_guards_vault_changes(browser_app, ui):
    browser_app.app.write(browser_app.vault, "Draft.md", "Original")
    other = browser_app.app.vault("OtherDockVault")
    browser_app.app.write(other, "Draft.md", "Other vault content")
    assert (
        await browser_app.app.request_async("/api/vaults/rescan", "POST", {})
    ).status_code == 200
    store = thread(browser_app, "dock-chat", [("Earlier question", "Earlier answer")])
    with idle_provider() as provider:
        # Only external model timing is controlled. Chat, file reads/writes and locks stay real.
        configure_provider(browser_app.app, provider)
        setting(browser_app.app, "default_model", "session-ops-model")
        await load_thread(ui, browser_app, "dock-chat")
        explorer = await open_explorer(ui, browser_app)
        await explorer.get_by_role("button", name="Use split view", exact=True).click()
        await explorer.locator('[data-vault-path-picker-select="Draft.md"]').click()
        reader = ui.get_by_role("dialog").filter(
            has=ui.locator("#vault-file-modal-path")
        )
        await reader.get_by_role("button", name="Edit Markdown", exact=True).click()
        editor = reader.locator("#vault-file-modal-editor")
        await editor.fill("Retain unsaved work")
        await ui.locator("#chat-input").click()
        await ui.locator("#chat-input").fill("Continue the conversation")
        await ui.get_by_role("button", name="Send message", exact=True).click()
        assert await asyncio.to_thread(provider.started.wait, 15)
        await expect(editor).not_to_be_editable()
        await expect(editor).to_have_value("Retain unsaved work")
        provider.release.set()
        await expect(
            ui.get_by_role("button", name="Send message", exact=True)
        ).to_be_enabled(timeout=20000)
        await expect(editor).to_be_editable()
        await expect(
            ui.locator("#chat-messages").get_by_text("idle response", exact=True)
        ).to_be_visible()
        await expect(editor).to_have_value("Retain unsaved work")
        ui.once("dialog", lambda dialog: dialog.dismiss())
        await ui.locator("#vault-selector").select_option(other.name)
        await expect(ui.locator("#vault-selector")).to_have_value(
            browser_app.vault.name
        )
        await expect(editor).to_have_value("Retain unsaved work")
        assert (browser_app.vault / "Draft.md").read_text() == "Original"
        ui.once("dialog", lambda dialog: dialog.accept())
        await ui.locator("#vault-selector").select_option(other.name)
        await expect(ui.locator("#vault-selector")).to_have_value(other.name)
        await expect(reader).to_have_count(0)
        assert store.get_session_by_id("dock-chat").vault_name == browser_app.vault.name
        explorer = ui.get_by_role("dialog", name="Vault Explorer", exact=True)
        await expect(explorer).to_be_visible()
        await expect(
            ui.get_by_role("separator", name="Resize explorer")
        ).to_be_visible()
        await explorer.locator('[data-vault-path-picker-select="Draft.md"]').click()
        await reader.get_by_role("button", name="Edit Markdown", exact=True).click()
        await expect(editor).to_have_value("Other vault content")
        await editor.fill("Edited in other vault")
        await reader.get_by_role("button", name="Save file", exact=True).click()
        await expect(
            reader.locator("#vault-file-modal-preview").get_by_text(
                "Edited in other vault", exact=True
            )
        ).to_be_visible()
        assert (other / "Draft.md").read_text() == "Edited in other vault"
        assert (browser_app.vault / "Draft.md").read_text() == "Original"
        await ui.locator("#vault-selector").select_option(browser_app.vault.name)
        await expect(explorer).to_be_visible()
        await expect(reader).to_have_count(0)
        await explorer.locator('[data-vault-path-picker-select="Draft.md"]').click()
        await reader.get_by_role("button", name="Edit Markdown", exact=True).click()
        await expect(editor).to_have_value("Original")


async def test_session_selection_and_new_chat_preserve_docked_file_edits(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "Draft.md", "Original")
    thread(browser_app, "dock-first", [("First question", "First answer")])
    thread(browser_app, "dock-second", [("Second question", "Second answer")])
    await load_thread(ui, browser_app, "dock-first")
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("button", name="Use split view", exact=True).click()
    await load_thread(ui, browser_app, "dock-second")
    await expect(
        ui.locator("#chat-messages").get_by_text("Second answer", exact=True)
    ).to_be_visible()
    await expect(explorer).to_be_visible()
    await explorer.locator('[data-vault-path-picker-select="Draft.md"]').click()
    reader = ui.get_by_role("dialog").filter(has=ui.locator("#vault-file-modal-path"))
    await reader.get_by_role("button", name="Edit Markdown", exact=True).click()
    editor = reader.locator("#vault-file-modal-editor")
    await editor.fill("Retained across conversations")
    await load_thread(ui, browser_app, "dock-first")
    await expect(
        ui.locator("#chat-messages").get_by_text("First answer", exact=True)
    ).to_be_visible()
    # Loading history uses the existing preview/read-only lock; return to editing afterward.
    await reader.get_by_role("button", name="Edit Markdown", exact=True).click()
    await expect(editor).to_be_visible()
    await expect(editor).to_be_editable()
    await expect(editor).to_have_value("Retained across conversations")
    dialog = await session_list(ui, browser_app)
    await dialog.get_by_role("button", name="New session", exact=True).click()
    await expect(dialog).to_have_count(0)
    await expect(
        ui.locator("#chat-messages").get_by_text("First answer", exact=True)
    ).to_have_count(0)
    await expect(ui.locator("#chat-input")).to_be_editable()
    await expect(ui.get_by_role("separator", name="Resize explorer")).to_be_visible()
    await expect(editor).to_have_value("Retained across conversations")
    await reader.get_by_role("button", name="Save file", exact=True).click()
    await expect(
        reader.locator("#vault-file-modal-preview").get_by_text(
            "Retained across conversations", exact=True
        )
    ).to_be_visible()
    assert (
        browser_app.vault / "Draft.md"
    ).read_text() == "Retained across conversations"
