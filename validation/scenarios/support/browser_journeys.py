"""Reusable UI navigation; actions and expected outcomes stay in the scenarios."""

from playwright.async_api import Page, expect


async def open_settings(page: Page, url: str, key: str):
    await page.goto(url)
    await page.get_by_role("button", name="System", exact=True).click()
    search = page.get_by_placeholder("Filter settings…")
    if not await search.is_visible():
        await page.get_by_text("Application Settings", exact=True).click()
    await search.fill(key)
    # The API setting key is the stable identity; CSS styling/layout is not.
    card = page.locator(f'[data-setting="{key}"]')
    await expect(card).to_be_visible()
    return card


async def open_document_import(page: Page, url: str, vault: str, name: str):
    await page.goto(url)
    await page.locator("#vault-selector").select_option(vault)
    await page.get_by_role("button", name="Open vault explorer", exact=True).click()
    explorer = page.get_by_role("dialog", name="Vault Explorer", exact=True)
    await explorer.get_by_role("checkbox", name=f"Select {name}", exact=True).check()
    await explorer.get_by_role("button", name="Import", exact=True).click()
    await explorer.get_by_role(
        "menuitem", name="Import to Markdown", exact=True
    ).click()
    form = explorer.get_by_role("form", name="Import to Markdown")
    await expect(form).to_be_visible()
    return form
