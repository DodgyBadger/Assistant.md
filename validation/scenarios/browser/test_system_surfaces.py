"""Real rendered connections, independently loaded panels and diagnostic details."""

import asyncio

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_fullstack import hold_delivery
from validation.scenarios.support.google_connections import grant

pytestmark = pytest.mark.scenario


async def connections(ui, application):
    await ui.goto(application.url)
    await ui.get_by_role("button", name="System", exact=True).click()
    await ui.locator("#config-connections-section > summary").click()


async def test_google_capability_changes_must_be_saved_before_authorization(
    browser_app, ui
):
    await connections(ui, browser_app)
    await ui.get_by_role("button", name="Add Google", exact=True).click()
    form = ui.locator('form[data-google-id="draft"]')
    await form.get_by_label("Connection name").fill("Browser Google")
    await form.get_by_label("OAuth client ID", exact=True).fill(
        "browser.apps.googleusercontent.com"
    )
    await form.get_by_label("OAuth client secret (write-only)").fill(
        "synthetic-browser-secret"
    )
    browser_app.app.evidence.register_secret("synthetic-browser-secret")
    await form.get_by_role("button", name="Save Google settings").click()
    saved = ui.locator('form[data-google-id]:not([data-google-id="draft"])')
    await expect(saved).to_have_count(1)
    if not await saved.is_visible():
        await ui.locator("#google-connections-list > details > summary").click()
    await expect(
        saved.get_by_role("button", name="Authorize Google", exact=True)
    ).to_be_enabled()
    await saved.get_by_label("Allow draft creation").check()
    await saved.get_by_role("button", name="Authorize Google", exact=True).click()
    await expect(ui.locator("#connections-feedback")).to_contain_text("Save")
    rows = browser_app.app.request("/api/system/connections/google/connections").json()
    if isinstance(rows, dict):
        rows = rows["connections"]
    assert len(rows) == 1 and not rows[0]["gmail"]["draft_creation_enabled"]
    await saved.get_by_role("button", name="Save Google settings").click()
    await expect(
        saved.get_by_role("button", name="Save Google settings")
    ).to_be_enabled()
    rows = browser_app.app.request("/api/system/connections/google/connections").json()
    if isinstance(rows, dict):
        rows = rows["connections"]
    assert rows[0]["gmail"]["draft_creation_enabled"]


async def test_connected_readonly_google_grant_shows_draft_reauthorization_warning(
    browser_app, ui
):
    app = browser_app.app
    response = app.request(
        "/api/system/connections/google/connections",
        method="POST",
        data={
            "display_name": "Readonly Browser Gmail",
            "client_id": "readonly.apps.googleusercontent.com",
            "gmail": {"draft_creation_enabled": True},
        },
    )
    assert response.status_code == 200, response.text
    from core.identity import LOCAL_USER_AUTHORITY
    from core.runtime.state import get_runtime_context

    service = get_runtime_context().google_connection
    service.set_client_secret(
        LOCAL_USER_AUTHORITY,
        "synthetic-client-secret",
        response.json()["connection_id"],
    )
    # A saved external grant is initial fixture input, not evidence of an OAuth
    # exchange. Real encrypted storage/readiness services compute the UI warning.
    service.save_token_state(
        LOCAL_USER_AUTHORITY,
        grant(app, "browser-readonly"),
        response.json()["connection_id"],
    )
    await connections(ui, browser_app)
    summary = ui.locator("#google-connections-list summary").filter(
        has_text="Readonly Browser Gmail"
    )
    await summary.click()
    await expect(
        ui.get_by_text("Connected; reauthorize to enable Gmail drafts", exact=False)
    ).to_be_visible()
    await expect(
        ui.get_by_role("button", name="Reauthorize Google", exact=True)
    ).to_be_enabled()


async def test_system_sections_load_while_unrelated_status_response_is_delayed(
    browser_app, ui
):
    async with hold_delivery(
        ui, browser_app.app, browser_app.url + "/api/status"
    ) as status:
        await ui.goto(browser_app.url)
        await asyncio.wait_for(status.held.wait(), 10)
        await ui.get_by_role("button", name="System", exact=True).click()
        await ui.locator("#config-settings-section > summary").click()
        await ui.get_by_placeholder("Filter settings…").fill(
            "browser_max_calls_per_turn"
        )
        await expect(
            ui.get_by_role("button", name="Edit setting", exact=True)
        ).to_be_enabled()
        await ui.locator("#config-models-section > summary").click()
        await expect(
            ui.get_by_role("button", name="Add Model", exact=True)
        ).to_be_enabled()
        status.release.set()


async def test_activity_metadata_is_expandable_complete_and_escaped(browser_app, ui):
    from core.logger import UnifiedLogger

    long = (
        "Browser metadata "
        + ("observed value " * 60)
        + '<script id="injected-script">bad()</script>'
    )
    # Real application logger output is the source. The fixture supplies an event
    # input to the production logger; it does not forge a browser API response.
    UnifiedLogger(tag="browser-input").info(
        "Browser metadata record",
        data={"event": "browser_long_metadata", "detail": long},
    )
    await ui.goto(browser_app.url)
    await ui.get_by_role("button", name="System", exact=True).click()
    await ui.locator("#config-activity-section > summary").click()
    await ui.get_by_role("button", name="Refresh Activity Log", exact=True).click()
    detail = (
        ui.locator("details")
        .filter(has=ui.locator("summary").filter(has_text="detail=Browser metadata"))
        .last
    )
    await detail.locator("summary").click()
    await expect(detail.locator("pre")).to_have_text(long)
    assert await ui.locator("script#injected-script").count() == 0
