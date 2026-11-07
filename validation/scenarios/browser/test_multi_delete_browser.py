"""The real approval card exposes one normalized recursive deletion selection."""

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_sessions import load_thread
from validation.support.chat.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.support.chat.inline_edit_reviews import request_review

pytestmark = pytest.mark.scenario


async def test_multi_delete_card_reviews_effective_selection_before_deleting(
    browser_app, ui
):
    app, vault = browser_app.app, browser_app.vault
    app.write(vault, "Archive/a.md", "reviewed note")
    app.write(vault, "Loose.md", "loose note")
    (vault / "Archive/Empty").mkdir()
    # Only the remote provider is controlled; real chat, deferred review, DOM,
    # submission, file mutation and persistence handle the operation.
    with code_tool_provider() as server:
        configure_provider(app, server)
        review = await request_review(
            app,
            server,
            vault.name,
            "browser-multi-delete",
            {
                "operation": "delete",
                "paths": ["Archive", "Archive/a.md", "Archive", "Loose.md"],
            },
        )
        await load_thread(ui, browser_app, "browser-multi-delete")
        card = ui.locator(f'[data-deferred-review-card="{review["artifact_ref"]}"]')
        await expect(card).to_be_visible()
        targets = card.get_by_role("listitem")
        await expect(targets).to_have_count(2)
        await expect(targets.filter(has_text="Archive")).to_have_count(1)
        await expect(targets.filter(has_text="Loose.md")).to_have_count(1)
        await expect(targets.filter(has_text="Archive/a.md")).to_have_count(0)
        assert (vault / "Archive/a.md").exists() and (vault / "Loose.md").exists()
        await card.get_by_role("button", name="Approve", exact=True).click()
        async with ui.expect_response("**/submit") as submitted:
            await card.locator("[data-deferred-review-submit]").click()
        response = await submitted.value
        assert response.status == 200
        payload = await response.json()
        assert (await app.wait_task(payload["task"]["task_id"]))[
            "status"
        ] == "completed"
        assert not (vault / "Archive").exists() and not (vault / "Loose.md").exists()
