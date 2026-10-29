"""File navigation, reference context and quoting through the real app UI."""

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_fullstack import (
    read_file,
    select_passage,
)

pytestmark = pytest.mark.scenario


def documents(application):
    app, vault = application.app, application.vault
    app.write(
        vault,
        "Notes/A.md",
        "# Source A\n\nFirst passage.\n\nAnother passage.\n\n[[B.md]]\n\n[Sibling](B.md)\n\n[Other label](../Other/C.md)\n",
    )
    app.write(vault, "Notes/B.md", "# Source B\n\nSibling evidence.\n")
    app.write(vault, "B.md", "Wrong root document")
    app.write(vault, "Other/C.md", "# Source C\n\nOther evidence.")


async def test_relative_wikilinks_keep_source_context_and_back_history(browser_app, ui):
    documents(browser_app)
    reader = await read_file(ui, browser_app, "Notes/A.md")
    await reader.get_by_role("link", name="B.md", exact=True).click()
    await expect(reader.locator("#vault-file-modal-path")).to_have_text("B.md")
    await reader.get_by_role("button", name="Back", exact=True).click()
    await reader.get_by_role("link", name="Sibling", exact=True).click()
    await expect(reader.locator("#vault-file-modal-path")).to_have_text("Notes/B.md")
    await expect(reader.locator("#vault-file-modal-preview")).to_contain_text(
        "Sibling evidence."
    )
    await reader.get_by_role("button", name="Back", exact=True).click()
    await expect(reader.locator("#vault-file-modal-path")).to_have_text("Notes/A.md")
    await reader.get_by_role("link", name="Other label", exact=True).click()
    await expect(reader.locator("#vault-file-modal-path")).to_have_text("Other/C.md")
    await reader.get_by_role("button", name="Browse vault tree").click()
    await expect(
        ui.get_by_role("dialog", name="Vault Explorer", exact=True)
    ).to_be_visible()


async def test_quote_append_preserves_draft_and_accumulates_ordered_blocks(
    browser_app, ui
):
    documents(browser_app)
    reader = await read_file(ui, browser_app, "Notes/A.md")
    # A pre-existing draft is user input, not synthetic app state.
    await reader.get_by_role("button", name="Close", exact=True).click()
    await ui.locator("#chat-input").fill("Existing draft")
    reader = await read_file(ui, browser_app, "Notes/A.md")
    await select_passage(ui, reader, "First passage.", "First instruction\nSecond line")
    await ui.get_by_role("button", name="Add to draft", exact=True).click()
    await expect(reader).to_be_visible()
    await expect(ui.locator("#chat-input")).to_have_value(
        "Existing draft\n\n[[Notes/A.md]]\n\n> First passage.\n\nFirst instruction\nSecond line"
    )
    await select_passage(ui, reader, "Another passage.")
    await ui.get_by_role("button", name="Add to draft", exact=True).click()
    value = await ui.locator("#chat-input").input_value()
    assert value.index("First passage.") < value.index("Another passage.")
    assert value.endswith("[[Notes/A.md]]\n\n> Another passage.")
    assert not browser_app.app.request(
        "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
    ).json()


@pytest.mark.parametrize("width,height", [(1440, 1000), (390, 640), (390, 320)])
async def test_selection_prompt_keyboard_newline_and_viewport_bounds(
    browser_app, ui, width, height
):
    documents(browser_app)
    await ui.set_viewport_size({"width": width, "height": height})
    reader = await read_file(ui, browser_app, "Notes/A.md")
    await select_passage(ui, reader, "First passage.", "First line")
    entry = ui.get_by_label("Instruction (optional)")
    await entry.press("End")
    await entry.press("Enter")
    await entry.press_sequentially("Second line")
    await expect(entry).to_have_value("First line\nSecond line")
    panel = ui.locator("#vault-selection-panel")
    await expect(panel).to_be_in_viewport()
    box = await panel.bounding_box()
    assert box and box["x"] >= -1 and box["y"] >= -1
    assert (
        box["x"] + box["width"] <= width + 1 and box["y"] + box["height"] <= height + 1
    )
    await ui.get_by_role("button", name="Discard selection prompt").click()
    await expect(reader).to_be_visible()


async def test_unsaved_file_changes_require_confirmation_and_save_is_durable(
    browser_app, ui
):
    documents(browser_app)
    reader = await read_file(ui, browser_app, "Notes/A.md")
    await reader.get_by_role("button", name="Edit Markdown").click()
    await reader.locator("#vault-file-modal-editor").fill(
        "# Updated source\n\nKeep these edits."
    )
    ui.once("dialog", lambda dialog: dialog.dismiss())
    await reader.get_by_role("button", name="Close", exact=True).click()
    await expect(reader.locator("#vault-file-modal-editor")).to_have_value(
        "# Updated source\n\nKeep these edits."
    )
    await reader.get_by_role("button", name="Save file").click()
    await expect(reader.locator("#vault-file-modal-preview")).to_contain_text(
        "Keep these edits."
    )
    assert (
        browser_app.vault / "Notes/A.md"
    ).read_text() == "# Updated source\n\nKeep these edits."
    await reader.get_by_role("button", name="Close", exact=True).click()
    await expect(reader).to_have_count(0)
    await expect(
        ui.get_by_role("dialog", name="Vault Explorer", exact=True)
    ).to_have_count(0)


async def test_quote_edit_mode_cancellation_preserves_instruction(browser_app, ui):
    documents(browser_app)
    reader = await read_file(ui, browser_app, "Notes/A.md")
    await select_passage(ui, reader, "First passage.", "Keep my instruction")
    ui.once("dialog", lambda dialog: dialog.dismiss())
    await reader.get_by_role("button", name="Edit Markdown").click()
    await expect(ui.get_by_label("Instruction (optional)")).to_have_value(
        "Keep my instruction"
    )
    ui.once("dialog", lambda dialog: dialog.accept())
    await reader.get_by_role("button", name="Edit Markdown").click()
    await expect(ui.get_by_label("Instruction (optional)")).to_be_hidden()
    await expect(reader.locator("#vault-file-modal-editor")).to_be_visible()
