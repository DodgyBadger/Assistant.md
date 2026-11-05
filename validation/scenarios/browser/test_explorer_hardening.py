"""Explorer context, modal lifecycle and current file contents remain coherent."""

import asyncio

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_fullstack import (
    hold_delivery,
    open_explorer,
    setting,
)
from validation.support.browser.browser_sessions import (
    load_thread,
    session_list,
    thread,
)
from validation.support.chat.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)

pytestmark = pytest.mark.scenario


async def test_preserved_dock_follows_current_session_workspace_and_new_chat(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "A/Only-A.md", "first workspace")
    browser_app.app.write(browser_app.vault, "B/Only-B.md", "second workspace")
    for name, workspace in (("first", "A"), ("second", "B")):
        thread(browser_app, name, [(f"Question {name}", f"Answer {name}")])
        response = await browser_app.app.request_async(
            f"/api/chat/sessions/{name}/workspace",
            "PATCH",
            {"vault_name": browser_app.vault.name, "path": workspace},
        )
        assert response.status_code == 200, response.text
    await load_thread(ui, browser_app, "first")
    await ui.locator("#file-reference-btn").click()
    explorer = ui.get_by_role("dialog", name="Vault Explorer", exact=True)
    await explorer.get_by_role("button", name="Use split view", exact=True).click()
    await expect(
        explorer.get_by_role("checkbox", name="Select Only-A.md", exact=True)
    ).to_be_visible()
    await load_thread(ui, browser_app, "second")
    await expect(
        ui.locator("#chat-messages").get_by_text("Answer second", exact=True)
    ).to_be_visible()
    await expect(
        explorer.get_by_role("checkbox", name="Select Only-B.md", exact=True)
    ).to_be_visible()
    await expect(
        explorer.get_by_role("checkbox", name="Select Only-A.md", exact=True)
    ).to_have_count(0)
    dialog = await session_list(ui, browser_app)
    await dialog.get_by_role("button", name="New session", exact=True).click()
    await expect(dialog).to_have_count(0)
    await expect(
        explorer.get_by_role("button", name="Workspace view", exact=True)
    ).to_be_disabled()
    await expect(
        explorer.get_by_role("checkbox", name="Select A", exact=True)
    ).to_be_visible()


async def test_overlay_escape_cannot_dismiss_an_inflight_confirmed_delete(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "Delete.md", "delete after confirmation")
    explorer = await open_explorer(ui, browser_app)
    await explorer.get_by_role("checkbox", name="Select Delete.md", exact=True).check()
    await explorer.get_by_role("button", name="Delete", exact=True).click()
    form = explorer.locator(
        '[data-vault-explorer-mutation-form][data-operation="delete"]'
    )
    # The app executes the real deletion; only delivery of its response is delayed.
    async with hold_delivery(
        ui,
        browser_app.app,
        browser_app.url + f"/api/vaults/{browser_app.vault.name}/paths/delete-batch",
        method="POST",
    ) as gate:
        await form.get_by_role("button", name="Delete", exact=True).click()
        await asyncio.wait_for(gate.held.wait(), 15)
        await expect(
            form.get_by_role("button", name="Delete", exact=True)
        ).to_be_disabled()
        await explorer.get_by_role("searchbox", name="Search files", exact=True).focus()
        await ui.keyboard.press("Escape")
        await expect(explorer).to_be_visible()
        await expect(form).to_be_visible()
        gate.release.set()
    await expect(
        explorer.get_by_role("checkbox", name="Select Delete.md", exact=True)
    ).to_have_count(0)
    assert not (browser_app.vault / "Delete.md").exists()


async def test_file_reader_overlay_traps_keyboard_focus_and_split_releases_it(
    browser_app, ui
):
    browser_app.app.write(browser_app.vault, "Reading.md", "Reading content")
    explorer = await open_explorer(ui, browser_app)
    await explorer.locator('[data-vault-path-picker-select="Reading.md"]').click()
    reader = ui.get_by_role("dialog").filter(has=ui.locator("#vault-file-modal-path"))
    await expect(reader.locator("#vault-file-modal-preview")).to_be_visible()
    await reader.locator("#vault-file-modal-preview").focus()
    await ui.keyboard.press("Tab")
    assert await reader.evaluate("element => element.contains(document.activeElement)")
    await reader.get_by_role("button", name="Back", exact=True).focus()
    await ui.keyboard.press("Shift+Tab")
    assert await reader.evaluate("element => element.contains(document.activeElement)")
    await reader.get_by_role("button", name="Use split view", exact=True).click()
    await reader.locator("#vault-file-modal-preview").focus()
    await ui.keyboard.press("Tab")
    assert not await reader.evaluate(
        "element => element.contains(document.activeElement)"
    )
    await ui.locator("#chat-input").focus()
    await ui.locator("#chat-input").fill("Usable beside the reader")
    await expect(ui.locator("#chat-input")).to_have_value("Usable beside the reader")


async def test_normal_composer_tool_write_refreshes_clean_docked_file(browser_app, ui):
    browser_app.app.write(browser_app.vault, "Reading.md", "Original content")
    # Only remote model output is controlled. The real agent, code runtime,
    # file mutation/snapshot service, task stream and browser reader stay real.
    with code_tool_provider() as provider:
        configure_provider(browser_app.app, provider)
        setting(browser_app.app, "default_model", "code-tool-model")
        setting(browser_app.app, "default_chat_mode", "inline_edit")
        provider.arguments = {
            "code": 'await file_read(operation="read", path="Reading.md")\nawait file_write(operation="write", path="Reading.md", content="Updated by the real tool", overwrite=True)\n"WRITE_OK"'
        }
        explorer = await open_explorer(ui, browser_app)
        await explorer.get_by_role("button", name="Use split view", exact=True).click()
        await explorer.locator('[data-vault-path-picker-select="Reading.md"]').click()
        reader = ui.get_by_role("dialog").filter(
            has=ui.locator("#vault-file-modal-path")
        )
        await expect(reader.locator("#vault-file-modal-preview")).to_contain_text(
            "Original content"
        )
        await ui.locator("#chat-input").fill("Update the open note")
        await ui.get_by_role("button", name="Send message", exact=True).click()
        await expect(
            ui.get_by_role("button", name="Send message", exact=True)
        ).to_be_enabled(timeout=30000)
        assert (
            browser_app.vault / "Reading.md"
        ).read_text() == "Updated by the real tool"
        await expect(reader.locator("#vault-file-modal-preview")).to_contain_text(
            "Updated by the real tool"
        )


async def test_older_workspace_save_cannot_replace_new_session_context(browser_app, ui):
    for path in ("A/First.md", "B/Second.md", "C/Chosen.md"):
        browser_app.app.write(browser_app.vault, path, path)
    for name, workspace in (("first", "A"), ("second", "B")):
        thread(browser_app, name, [(f"Question {name}", f"Answer {name}")])
        response = await browser_app.app.request_async(
            f"/api/chat/sessions/{name}/workspace",
            "PATCH",
            {"vault_name": browser_app.vault.name, "path": workspace},
        )
        assert response.status_code == 200, response.text
    await load_thread(ui, browser_app, "first")
    await ui.locator("#file-reference-btn").click()
    explorer = ui.get_by_role("dialog", name="Vault Explorer", exact=True)
    await explorer.get_by_role("button", name="Use split view", exact=True).click()
    await explorer.get_by_role("button", name="Workspace view", exact=True).click()
    await explorer.get_by_role("checkbox", name="Select C", exact=True).check()
    # Only response timing changes; the old session's real workspace write commits.
    async with hold_delivery(
        ui,
        browser_app.app,
        browser_app.url + "/api/chat/sessions/first/workspace",
        method="PATCH",
    ) as gate:
        await explorer.get_by_role(
            "button", name="Set as workspace", exact=True
        ).click()
        await asyncio.wait_for(gate.held.wait(), 15)
        await load_thread(ui, browser_app, "second")
        await expect(ui.locator("#workspace-path-input")).to_have_value("B")
        gate.release.set()
    await ui.wait_for_load_state("networkidle")
    await expect(ui.locator("#workspace-path-input")).to_have_value("B")
    await explorer.get_by_role("button", name="Workspace view", exact=True).click()
    await expect(
        explorer.get_by_role("checkbox", name="Select Second.md", exact=True)
    ).to_be_visible()
    for name, expected_path in (("first", "C"), ("second", "B")):
        response = await browser_app.app.request_async(
            f"/api/chat/sessions/{name}", params={"vault_name": browser_app.vault.name}
        )
        assert response.status_code == 200
        assert response.json()["workspace"]["path"] == expected_path


async def test_older_workspace_save_cannot_replace_a_newer_same_session_choice(
    browser_app, ui
):
    for path in ("A/First.md", "C/Chosen.md", "D/Later.md"):
        browser_app.app.write(browser_app.vault, path, path)
    thread(browser_app, "first", [("Question first", "Answer first")])
    response = await browser_app.app.request_async(
        "/api/chat/sessions/first/workspace",
        "PATCH",
        {"vault_name": browser_app.vault.name, "path": "A"},
    )
    assert response.status_code == 200, response.text
    await load_thread(ui, browser_app, "first")
    await ui.locator("#file-reference-btn").click()
    explorer = ui.get_by_role("dialog", name="Vault Explorer", exact=True)
    await explorer.get_by_role("button", name="Use split view", exact=True).click()
    await explorer.get_by_role("button", name="Workspace view", exact=True).click()
    await explorer.get_by_role("checkbox", name="Select C", exact=True).check()
    # Both writes execute normally. Only the first response is delivered late.
    async with hold_delivery(
        ui,
        browser_app.app,
        browser_app.url + "/api/chat/sessions/first/workspace",
        method="PATCH",
        first_only=True,
    ) as gate:
        await explorer.get_by_role(
            "button", name="Set as workspace", exact=True
        ).click()
        await asyncio.wait_for(gate.held.wait(), 15)
        await explorer.get_by_role("checkbox", name="Select C", exact=True).uncheck()
        await explorer.get_by_role("checkbox", name="Select D", exact=True).check()
        await explorer.get_by_role(
            "button", name="Set as workspace", exact=True
        ).click()
        await expect(ui.locator("#workspace-path-input")).to_have_value("D")
        gate.release.set()
    await ui.wait_for_load_state("networkidle")
    await expect(ui.locator("#workspace-path-input")).to_have_value("D")
    response = await browser_app.app.request_async(
        "/api/chat/sessions/first", params={"vault_name": browser_app.vault.name}
    )
    assert response.status_code == 200
    assert response.json()["workspace"]["path"] == "D"
