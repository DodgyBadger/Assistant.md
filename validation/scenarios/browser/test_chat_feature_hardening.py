"""Chat export fidelity and recoverable bookmark failures through real UI actions."""

import asyncio

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_fullstack import hold_delivery, lose_connection
from validation.support.browser.browser_sessions import (
    load_thread,
    session_list,
    thread,
)

pytestmark = pytest.mark.scenario


async def test_excerpt_copy_preserves_preformatted_indentation(browser_app, ui):
    code = "    first = 1\n    print(first)\n"
    source = f"A code sample:\n\n```python\n{code}```\n\nAfter the sample."
    thread(browser_app, "excerpt-code", [("Show a code sample", source)])
    await ui.context.grant_permissions(["clipboard-read", "clipboard-write"])
    await load_thread(ui, browser_app, "excerpt-code")
    copy = (
        ui.locator("#chat-messages")
        .get_by_role("button", name="Copy to clipboard", exact=True)
        .last
    )
    await copy.focus()
    await ui.keyboard.press("Alt+ArrowDown")
    dialog = ui.get_by_role("dialog", name="Copy excerpt", exact=True)
    await dialog.get_by_role("button", name="Paragraph 2", exact=True).click()
    await dialog.get_by_role("button", name="Copy selected", exact=True).click()
    await expect(dialog).to_have_count(0)
    # Code indentation and its final newline are exported user content, not UI copy.
    assert await ui.evaluate("navigator.clipboard.readText()") == code


async def test_session_favorite_transport_failure_is_visible_and_retryable(
    browser_app, ui
):
    thread(browser_app, "favorite-retry", [("Question", "Durable answer")])
    dialog = await session_list(ui, browser_app)
    row = dialog.locator('[data-session-browser-row-id="favorite-retry"]')
    favorite = row.get_by_role("button", name="Favorite session", exact=True)
    async with lose_connection(
        ui,
        browser_app.app,
        "**/api/chat/sessions/favorite-retry/favorite",
        method="PATCH",
    ) as dropped:
        await favorite.click()
        await expect(dialog.get_by_role("alert")).to_be_visible()
        await expect(favorite).to_be_enabled()
        assert len(dropped) == 1
    sessions = browser_app.app.request(
        "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
    ).json()
    assert (
        next(item for item in sessions if item["session_id"] == "favorite-retry")[
            "favorite"
        ]
        is False
    )
    await favorite.click()
    await expect(
        row.get_by_role("button", name="Remove favorite", exact=True)
    ).to_have_attribute("aria-pressed", "true")
    await expect(dialog.get_by_role("alert")).to_have_count(0)
    sessions = browser_app.app.request(
        "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
    ).json()
    assert (
        next(item for item in sessions if item["session_id"] == "favorite-retry")[
            "favorite"
        ]
        is True
    )


async def test_pending_session_favorite_stays_locked_after_vault_round_trip(
    browser_app, ui
):
    thread(browser_app, "favorite-owner", [("Question", "Answer")])
    other = browser_app.app.vault("FavoriteRoundTrip")
    assert (
        await browser_app.app.request_async("/api/vaults/rescan", "POST", {})
    ).status_code == 200
    dialog = await session_list(ui, browser_app)
    pattern = "**/api/chat/sessions/favorite-owner/favorite"
    async with hold_delivery(
        ui, browser_app.app, pattern, method="PATCH", first_only=True
    ) as held:
        await dialog.get_by_role("button", name="Favorite session", exact=True).click()
        await asyncio.wait_for(held.held.wait(), 10)
        await dialog.get_by_role(
            "button", name="Close chat settings", exact=True
        ).click()
        await ui.locator("#vault-selector").select_option(other.name)
        await ui.locator("#vault-selector").select_option(browser_app.vault.name)
        dialog = await session_list(ui, browser_app)
        row = dialog.locator('[data-session-browser-row-id="favorite-owner"]')
        opposite = row.get_by_role("button", name="Remove favorite", exact=True)
        # The server has committed the first toggle; only its response is held.
        # A replacement control must not admit an opposite toggle that the older
        # response could later overwrite in the UI.
        await expect(opposite).to_be_disabled()
        held.release.set()
        await expect(opposite).to_be_enabled()
    await opposite.click()
    await expect(
        row.get_by_role("button", name="Favorite session", exact=True)
    ).to_have_attribute("aria-pressed", "false")
    sessions = browser_app.app.request(
        "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
    ).json()
    assert (
        next(item for item in sessions if item["session_id"] == "favorite-owner")[
            "favorite"
        ]
        is False
    )
