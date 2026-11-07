"""A user can approve a binary copy and choose its exact destination."""

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_sessions import load_thread
from validation.support.chat.code_tool_chat import (
    code_tool_provider,
    configure_provider,
)
from validation.support.chat.inline_edit_reviews import request_review

pytestmark = pytest.mark.scenario


async def test_binary_copy_card_uses_approved_destination(browser_app, ui):
    app, vault = browser_app.app, browser_app.vault
    content = b"PK\x03\x04\x00\xfforiginal source"
    (vault / "source.odt").write_bytes(content)
    # Only inference is controlled; chat review, browser input, submission,
    # tool execution, file persistence and recovery recording remain real.
    with code_tool_provider() as server:
        configure_provider(app, server)
        review = await request_review(
            app,
            server,
            vault.name,
            "browser-copy",
            {"operation": "copy", "path": "source.odt", "destination": "proposed.odt"},
        )
        await load_thread(ui, browser_app, "browser-copy")
        card = ui.locator(f'[data-deferred-review-card="{review["artifact_ref"]}"]')
        await expect(card).to_be_visible()
        await card.locator('[data-arg-name="destination"]').fill("Archive/approved.odt")
        assert not (vault / "proposed.odt").exists()
        assert not (vault / "Archive/approved.odt").exists()
        await card.get_by_role("button", name="Approve", exact=True).click()
        async with ui.expect_response("**/submit") as submitted:
            await card.locator("[data-deferred-review-submit]").click()
        response = await submitted.value
        assert response.status == 200
        payload = await response.json()
        assert (await app.wait_task(payload["task"]["task_id"]))[
            "status"
        ] == "completed"
        assert (vault / "Archive/approved.odt").read_bytes() == content
        assert (vault / "source.odt").read_bytes() == content
        assert not (vault / "proposed.odt").exists()
