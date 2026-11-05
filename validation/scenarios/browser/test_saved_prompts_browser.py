"""Saved prompt selection edits a draft; only explicit Send creates a turn."""

import asyncio
import re
from pathlib import Path

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_fullstack import (
    hold_delivery,
    setting,
)
from validation.support.browser.browser_sessions import load_thread, thread
from validation.support.chat.chat_idle_provider import idle_provider
from validation.support.chat.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def open_saved_prompts(ui):
    await ui.locator("#send-btn").focus()
    await ui.keyboard.press("Alt+ArrowDown")


async def new_workspace(ui, browser_app):
    await ui.goto(browser_app.url)
    await ui.locator("#vault-selector").select_option(browser_app.vault.name)
    await ui.get_by_role("button", name="Add workspace", exact=True).click()
    await ui.get_by_role("textbox", name="Workspace path", exact=True).fill("Project")
    await ui.get_by_role("textbox", name="Workspace path", exact=True).press("Enter")


@pytest.mark.parametrize("width", [1280, 390], ids=["desktop", "mobile"])
async def test_saved_prompt_inserts_without_consuming_draft_or_attachments(
    browser_app, ui, width
):
    # A usable remote-model configuration makes accidental submission observable.
    with idle_provider() as provider:
        browser_app.app.write(
            browser_app.vault,
            "Project/prompts.md",
            "## Insert\nPrompt body\n\n## Other\nUnselected body",
        )
        configure_provider(browser_app.app, provider)
        setting(browser_app.app, "default_model", "session-ops-model")
        await ui.set_viewport_size({"width": width, "height": 900})
        await new_workspace(ui, browser_app)
        draft = ui.locator("#chat-input")
        await draft.fill("Before After")
        # Native selection is input to the real composer, not a substituted app handler.
        await draft.evaluate("element => element.setSelectionRange(7, 12)")
        await ui.locator("#attach-input").set_input_files(
            Path("validation/fixtures/files/test_image.jpg")
        )
        await open_saved_prompts(ui)
        await ui.get_by_role("menuitem", name="Insert", exact=True).focus()
        await ui.keyboard.press("Enter")
        # Exact draft text protects user-authored input, not product prose.
        await expect(draft).to_have_value("Before Prompt bodyAfter")
        await expect(draft).to_be_focused()
        await expect(ui.locator("#attach-count-badge")).to_have_text("1")
        assert (
            browser_app.app.request(
                "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
            ).json()
            == []
        )
        await draft.fill("Retain this draft")
        send = ui.locator("#send-btn")
        await send.hover()
        await ui.mouse.down()
        await expect(
            ui.get_by_role("menuitem", name="Insert", exact=True)
        ).to_be_visible()
        await ui.mouse.up()
        await expect(draft).to_have_value("Retain this draft")
        await ui.keyboard.press("Escape")
        await expect(
            ui.get_by_role("menuitem", name="Insert", exact=True)
        ).to_have_count(0)
        await ui.clock.install()
        await send.hover()
        await ui.mouse.down()
        box = await send.bounding_box()
        await ui.mouse.move(box["x"] + box["width"] + 20, box["y"] + box["height"] / 2)
        await ui.clock.fast_forward(700)
        await ui.mouse.up()
        await expect(
            ui.get_by_role("menuitem", name="Insert", exact=True)
        ).to_have_count(0)
        await expect(draft).to_have_value("Retain this draft")
        assert (
            browser_app.app.request(
                "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
            ).json()
            == []
        )
        (browser_app.vault / "Project/prompts.md").unlink()
        await open_saved_prompts(ui)
        await expect(ui.get_by_role("menuitem")).to_have_count(0)
        await ui.keyboard.press("Escape")
        await expect(draft).to_have_value("Retain this draft")
        assert provider.requests == []


async def test_saved_prompt_only_enters_model_context_after_explicit_send_and_stop_stays_available(
    browser_app, ui
):
    browser_app.app.write(
        browser_app.vault,
        "Project/prompts.md",
        "## Chosen\nChosen body\n\n## Unused\nUNSELECTED_LIBRARY_SENTINEL",
    )
    with idle_provider() as provider:
        # Only the external LLM is controlled; API, context, tasks and stores stay real.
        configure_provider(browser_app.app, provider)
        setting(browser_app.app, "default_model", "session-ops-model")
        await new_workspace(ui, browser_app)
        await open_saved_prompts(ui)
        await ui.get_by_role("menuitem", name="Chosen", exact=True).click()
        await ui.locator("#chat-input").fill("Chosen body edited")
        assert not provider.requests
        await ui.locator("#send-btn").click()
        assert await asyncio.to_thread(provider.started.wait, 15)
        # The library itself must not become automatic model instructions.
        import json

        messages = json.dumps(provider.requests[0]["messages"])
        assert "Chosen body edited" in messages
        assert "UNSELECTED_LIBRARY_SENTINEL" not in messages
        await open_saved_prompts(ui)
        await expect(ui.get_by_role("menuitem")).to_have_count(0)
        await expect(
            ui.get_by_role("button", name="Stop the active response", exact=True)
        ).to_be_enabled()
        await ui.locator("#send-btn").click()
        provider.release.set()
        await expect(
            ui.get_by_role("button", name="Send message", exact=True)
        ).to_be_enabled(timeout=20000)
        assert len(provider.requests) == 1


async def test_late_library_cannot_insert_into_another_session_and_reopening_reads_edits(
    browser_app, ui
):
    browser_app.app.write(
        browser_app.vault, "Project/prompts.md", "## First\nFirst body"
    )
    store = thread(browser_app, "first", [("First question", "First answer")])
    thread(browser_app, "second", [("Second question", "Second answer")])
    for session in ("first", "second"):
        store.set_session_workspace(
            session_id=session,
            vault_name=browser_app.vault.name,
            workspace_path="Project",
        )
    await load_thread(ui, browser_app, "first")
    # Finish automatic availability discovery before delaying the explicit menu read.
    await ui.wait_for_load_state("networkidle")
    async with hold_delivery(
        ui, browser_app.app, "**/saved-prompts?*", first_only=True
    ) as held:
        await open_saved_prompts(ui)
        await asyncio.wait_for(held.held.wait(), 10)
        await load_thread(ui, browser_app, "second")
        await ui.locator("#chat-input").fill("Second draft")
        held.release.set()
    await expect(ui.get_by_role("menuitem", name="First", exact=True)).to_have_count(0)
    await expect(ui.locator("#chat-input")).to_have_value("Second draft")
    browser_app.app.write(
        browser_app.vault, "Project/prompts.md", "## Updated\nUpdated body"
    )
    await open_saved_prompts(ui)
    await ui.get_by_role("menuitem", name="Updated", exact=True).click()
    await expect(ui.locator("#chat-input")).to_have_value("Second draftUpdated body")

    browser_app.app.write(
        browser_app.vault,
        "OtherProject/prompts.md",
        "## Different\nOther workspace body",
    )
    # Finish automatic availability discovery before delaying the explicit menu read.
    await ui.wait_for_load_state("networkidle")
    async with hold_delivery(
        ui, browser_app.app, "**/saved-prompts?*", first_only=True
    ) as held:
        await open_saved_prompts(ui)
        await asyncio.wait_for(held.held.wait(), 10)
        await ui.get_by_role("button", name=re.compile("^Open vault explorer")).click()
        explorer = ui.get_by_role("dialog", name="Vault Explorer", exact=True)
        await explorer.get_by_role("button", name="Vault root", exact=True).click()
        await explorer.get_by_role(
            "checkbox", name="Select OtherProject", exact=True
        ).check()
        await explorer.get_by_role(
            "button", name="Set as workspace", exact=True
        ).click()
        # The navigation target updates after the real workspace save completes.
        await expect(
            explorer.get_by_role("button", name="Workspace view", exact=True)
        ).to_have_attribute("data-vault-explorer-location", "OtherProject")
        await explorer.get_by_role("button", name="Close", exact=True).click()
        held.release.set()
    await expect(ui.get_by_role("menuitem", name="Updated", exact=True)).to_have_count(
        0
    )
    await open_saved_prompts(ui)
    await ui.get_by_role("menuitem", name="Different", exact=True).click()
    await expect(ui.locator("#chat-input")).to_have_value(
        "Second draftUpdated bodyOther workspace body"
    )
