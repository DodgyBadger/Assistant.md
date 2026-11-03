"""Selecting a stable paragraph range copies only that range, without native handles."""

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_sessions import load_thread, thread

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize("width", [1280, 390], ids=["desktop", "mobile"])
async def test_excerpt_range_survives_scrolling_and_preserves_full_copy(
    browser_app, ui, width
):
    paragraphs = [
        f"Paragraph {i}: " + ("Distinct sample text for scrolling. " * 10).strip()
        for i in range(1, 37)
    ]
    source = "\n\n".join(paragraphs)
    store = thread(browser_app, "excerpt-long", [("Request", source)])
    original = store.get_history("excerpt-long", browser_app.vault.name, mode="raw")
    await ui.set_viewport_size({"width": width, "height": 900})
    await ui.context.grant_permissions(["clipboard-read", "clipboard-write"])
    await load_thread(ui, browser_app, "excerpt-long")
    await ui.locator("#chat-input").fill("Keep this draft")
    copy = (
        ui.locator("#chat-messages")
        .get_by_role("button", name="Copy to clipboard", exact=True)
        .last
    )
    await copy.click()
    # Whole-message copy continues to preserve original user-owned source text.
    assert await ui.evaluate("navigator.clipboard.readText()") == source
    await ui.evaluate("navigator.clipboard.writeText('clipboard baseline')")
    await copy.hover()
    await ui.mouse.down()
    dialog = ui.get_by_role("dialog", name="Copy excerpt", exact=True)
    await expect(dialog).to_be_visible()
    await ui.mouse.up()
    assert await ui.evaluate("navigator.clipboard.readText()") == "clipboard baseline"
    await dialog.get_by_role("button", name="Paragraph 3", exact=True).click()
    last = dialog.get_by_role("button", name="Paragraph 28", exact=True)
    await last.scroll_into_view_if_needed()
    await last.click()
    assert await ui.evaluate("navigator.clipboard.readText()") == "clipboard baseline"
    # Diagnostic image for direct review, not a presentation snapshot oracle.
    await ui.screenshot(path=browser_app.app.evidence.artifacts / "excerpt-range.png")
    await dialog.get_by_role("button", name="Copy selected", exact=True).click()
    await expect(dialog).to_have_count(0)
    # Exact clipboard content is the exported excerpt contract, not UI wording.
    assert await ui.evaluate("navigator.clipboard.readText()") == "\n\n".join(
        paragraphs[2:28]
    )
    await expect(ui.locator("#chat-input")).to_have_value("Keep this draft")
    assert (
        store.get_history("excerpt-long", browser_app.vault.name, mode="raw")
        == original
    )


async def test_excerpt_keyboard_reverse_single_and_cancel_keep_clipboard_explicit(
    browser_app, ui
):
    source = "**First paragraph.**\n\nSecond paragraph.\n\nThird paragraph.\n\nFourth paragraph."
    thread(browser_app, "excerpt-short", [("Question", source)])
    await ui.context.grant_permissions(["clipboard-read", "clipboard-write"])
    await load_thread(ui, browser_app, "excerpt-short")
    copy = (
        ui.locator("#chat-messages")
        .get_by_role("button", name="Copy to clipboard", exact=True)
        .last
    )
    await ui.evaluate("navigator.clipboard.writeText('unchanged')")
    await copy.focus()
    await ui.keyboard.press("Alt+ArrowDown")
    dialog = ui.get_by_role("dialog", name="Copy excerpt", exact=True)
    await dialog.get_by_role("button", name="Paragraph 2", exact=True).click()
    await ui.keyboard.press("Escape")
    await expect(dialog).to_have_count(0)
    await expect(copy).to_be_focused()
    assert await ui.evaluate("navigator.clipboard.readText()") == "unchanged"
    await ui.keyboard.press("Alt+ArrowDown")
    await dialog.get_by_role("button", name="Paragraph 4", exact=True).click()
    await dialog.get_by_role("button", name="Paragraph 2", exact=True).click()
    await dialog.get_by_role("button", name="Copy selected", exact=True).click()
    await expect(dialog).to_have_count(0)
    assert (
        await ui.evaluate("navigator.clipboard.readText()")
        == "Second paragraph.\n\nThird paragraph.\n\nFourth paragraph."
    )
    await copy.focus()
    await ui.keyboard.press("Alt+ArrowDown")
    await dialog.get_by_role("button", name="Paragraph 1", exact=True).click()
    await dialog.get_by_role("button", name="Copy selected", exact=True).click()
    await expect(dialog).to_have_count(0)
    assert await ui.evaluate("navigator.clipboard.readText()") == "First paragraph."
