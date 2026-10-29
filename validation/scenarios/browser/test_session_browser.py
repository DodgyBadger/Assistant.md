"""Session browsing, canonical rendering, titles, search and map navigation."""

import asyncio
import re

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_fullstack import (
    deterministic_chat,
    hold_delivery,
    lose_connection,
)
from validation.scenarios.support.browser_sessions import (
    load_thread,
    map_checkpoint,
    session_list,
    session_options,
    thread,
)
from validation.scenarios.support.session_discovery import seed_discovery_sessions

pytestmark = pytest.mark.scenario


async def test_session_title_edit_and_content_search_preserve_real_history(
    browser_app, ui
):
    deterministic_chat(browser_app)
    thread(
        browser_app,
        "first",
        [("[A bracketed user prompt]", "Persisted answer otter")],
        title="Initial title",
    )
    thread(
        browser_app,
        "second",
        [("A different question", "A different answer")],
        title="Other title",
    )
    dialog = await session_list(ui, browser_app)
    row = dialog.locator('[data-session-browser-row-id="first"]')
    await row.get_by_role("button", name="Edit title", exact=True).click()
    await dialog.get_by_label("Session title", exact=True).fill("Renamed conversation")
    await dialog.get_by_label("Session title", exact=True).press("Enter")
    await expect(dialog.get_by_text("Renamed conversation", exact=True)).to_be_visible()
    await dialog.get_by_role("button", name="Session search mode").click()
    await dialog.get_by_role("menuitemradio", name="Contents", exact=True).click()
    await dialog.get_by_role("searchbox", name="Find sessions").fill("otter")
    await expect(
        dialog.locator('[data-session-browser-row-id="second"]')
    ).to_have_count(0)
    await expect(
        dialog.locator('[data-session-browser-row-id="first"]')
    ).to_be_visible()
    await dialog.locator('[data-session-browser-row-id="first"]').click()
    await expect(
        ui.locator("#chat-messages").get_by_text(
            "[A bracketed user prompt]", exact=True
        )
    ).to_be_visible()
    await ui.reload()
    await load_thread(ui, browser_app, "first")
    await expect(
        ui.locator("#chat-messages").get_by_text(
            "[A bracketed user prompt]", exact=True
        )
    ).to_be_visible()
    rows = browser_app.app.request(
        "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
    ).json()
    assert (
        next(r["title"] for r in rows if r["session_id"] == "first")
        == "Renamed conversation"
    )


async def test_late_vault_session_list_cannot_replace_current_vault(browser_app, ui):
    other = browser_app.app.vault("OtherBrowserVault")
    thread(
        browser_app,
        "first",
        [("First vault prompt", "First vault answer")],
        title="First vault session",
    )
    from core.runtime.state import get_runtime_context
    from validation.scenarios.support.session_map_journey_inputs import (
        conversation,
        seed_session,
    )

    store = get_runtime_context().chat_store
    seed_session(
        store,
        "foreign",
        other.name,
        conversation([("Other vault prompt", "Other vault answer")]),
    )
    store.set_session_title("foreign", other.name, "Other vault session")
    response = await browser_app.app.request_async(
        "/api/vaults/rescan", method="POST", data={}
    )
    assert response.status_code == 200, response.text
    async with hold_delivery(
        ui,
        browser_app.app,
        browser_app.url + f"/api/chat/sessions?vault_name={browser_app.vault.name}",
    ) as gate:
        await ui.goto(browser_app.url)
        await ui.locator("#vault-selector").select_option(browser_app.vault.name)
        await asyncio.wait_for(gate.held.wait(), 10)
        await ui.locator("#vault-selector").select_option(other.name)
        await ui.get_by_role("button", name="Open chat settings", exact=True).click()
        await expect(ui.get_by_text("Other vault session", exact=True)).to_be_visible()
        gate.release.set()
    await expect(ui.get_by_text("First vault session", exact=True)).to_have_count(0)
    await expect(ui.locator("#vault-selector")).to_have_value(other.name)


async def test_map_checkpoints_render_narrative_and_restore_focus_on_escape(
    browser_app, ui
):
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    seed_discovery_sessions(store, browser_app.vault.name)
    dialog = await session_list(ui, browser_app)
    button = dialog.locator('[data-session-browser-row-id="long"]').get_by_role(
        "button", name="Open session map", exact=True
    )
    await button.click()
    modal = ui.locator("#session-map-modal")
    await expect(
        modal.get_by_text("Current proposal remains exploratory", exact=False).first
    ).to_be_visible()
    await modal.get_by_label("Checkpoint", exact=True).select_option("old-map")
    await expect(
        modal.get_by_text("Quokka feasibility review", exact=False).first
    ).to_be_visible()
    await ui.keyboard.press("Escape")
    await expect(modal).to_have_count(0)
    await expect(
        ui.get_by_role("button", name="Open chat settings", exact=True)
    ).to_be_focused()
    assert await dialog.locator('[data-session-browser-row-id="hidden"]').count() == 0


async def test_older_timeline_rows_prepend_without_replacing_current_transcript(
    browser_app, ui
):
    pairs = [(f"Canonical question {i}", f"Canonical answer {i}") for i in range(65)]
    store = thread(browser_app, "long-history", pairs, title="Long history")
    # Checkpoint input selects the canonical timeline path; this scenario tests
    # UI pagination, not production map authorship (covered by core scenarios).
    map_checkpoint(store, "long-history", browser_app.vault.name, "timeline-map", 39)
    await load_thread(ui, browser_app, "long-history")
    messages = ui.locator("#chat-messages")
    await expect(
        messages.get_by_text("Canonical question 64", exact=True)
    ).to_be_visible()
    older = messages.get_by_role("button", name="Load older messages", exact=True)
    await expect(older).to_be_visible()
    await older.scroll_into_view_if_needed()
    anchor = messages.get_by_text("Canonical question 45", exact=True)
    before = await anchor.bounding_box()
    assert before
    await older.click()
    await expect(
        messages.get_by_text("Canonical question 64", exact=True)
    ).to_have_count(1)
    after = await anchor.bounding_box()
    assert after and abs(after["y"] - before["y"]) <= 2
    # A second page adds older canonical content rather than replacing the tail.
    await expect(
        messages.get_by_text("Canonical question 30", exact=True)
    ).to_be_visible()


async def test_session_load_transport_failure_is_visible_and_retry_loads_real_data(
    browser_app, ui
):
    thread(browser_app, "retry-load", [("A durable question", "A durable answer")])
    dialog = await session_list(ui, browser_app)
    async with lose_connection(
        ui, browser_app.app, browser_app.url + "/api/chat/sessions/retry-load?*"
    ) as drops:
        await dialog.locator('[data-session-browser-row-id="retry-load"]').click()
        await expect(ui.locator("#chat-messages")).to_contain_text("Failed")
        assert len(drops) == 1
    await load_thread(ui, browser_app, "retry-load")
    await expect(
        ui.locator("#chat-messages").get_by_text("A durable answer", exact=True)
    ).to_be_visible()


async def test_late_checkpoint_response_cannot_replace_newer_map_selection(
    browser_app, ui
):
    from core.runtime.state import get_runtime_context

    seed_discovery_sessions(get_runtime_context().chat_store, browser_app.vault.name)
    dialog = await session_list(ui, browser_app)
    await dialog.locator('[data-session-browser-row-id="long"]').get_by_role(
        "button", name="Open session map", exact=True
    ).click()
    modal = ui.locator("#session-map-modal")
    await expect(modal.get_by_label("Checkpoint", exact=True)).to_have_value("new-map")
    async with hold_delivery(
        ui, browser_app.app, "**/api/chat/sessions/long/map?*checkpoint_id=old-map"
    ) as gate:
        await modal.get_by_label("Checkpoint", exact=True).select_option("old-map")
        await asyncio.wait_for(gate.held.wait(), 10)
        # While a read is pending the selector is replaced by loading UI.
        # Exercise the reachable overlap: close the old map and reopen latest.
        await modal.get_by_role("button", name="Close", exact=True).click()
        dialog = await session_list(ui, browser_app)
        await dialog.locator('[data-session-browser-row-id="long"]').get_by_role(
            "button", name="Open session map", exact=True
        ).click()
        await expect(
            modal.get_by_text("Current proposal remains exploratory", exact=False).first
        ).to_be_visible()
        gate.release.set()
    await expect(modal.get_by_label("Checkpoint", exact=True)).to_have_value("new-map")
    await expect(
        modal.get_by_text("Quokka feasibility review", exact=False)
    ).to_have_count(0)


async def test_fork_action_creates_independent_persisted_conversation(browser_app, ui):
    thread(
        browser_app,
        "fork-source",
        [("First question", "First answer"), ("Second question", "Second answer")],
    )
    await load_thread(ui, browser_app, "fork-source")
    ui.once("dialog", lambda dialog: dialog.accept())
    await ui.get_by_role(
        "button", name="Fork session from this message", exact=True
    ).first.click()
    await expect(
        ui.locator("#chat-messages").get_by_text("First answer", exact=True)
    ).to_be_visible()
    await expect(
        ui.locator("#chat-messages").get_by_text("Second answer", exact=True)
    ).to_have_count(0)
    sessions = browser_app.app.request(
        "/api/chat/sessions", params={"vault_name": browser_app.vault.name}
    ).json()
    assert len(sessions) == 2
    fork = next(s for s in sessions if s["session_id"] != "fork-source")
    response = browser_app.app.request(
        f'/api/chat/sessions/{fork["session_id"]}',
        params={"vault_name": browser_app.vault.name},
    )
    assert response.status_code == 200, response.text
    assert "First answer" in response.text and "Second answer" not in response.text


async def test_pending_chat_mode_save_is_scoped_to_its_original_session(
    browser_app, ui
):
    thread(browser_app, "mode-source", [("Source prompt", "Source answer")])
    thread(browser_app, "mode-other", [("Other prompt", "Other answer")])
    await load_thread(ui, browser_app, "mode-source")
    dialog = await session_options(ui, browser_app)
    async with hold_delivery(
        ui,
        browser_app.app,
        browser_app.url + "/api/chat/sessions/mode-source/mode",
        method="PATCH",
    ) as gate:
        await ui.get_by_role("button", name="File approvals: Off", exact=True).click()
        await asyncio.wait_for(gate.held.wait(), 10)
        await dialog.locator('[data-session-browser-row-id="mode-other"]').click()
        gate.release.set()
    dialog = await session_options(ui, browser_app)
    await expect(
        ui.get_by_role("button", name="File approvals: Off", exact=True)
    ).to_be_visible()
    await dialog.locator('[data-session-browser-row-id="mode-source"]').click()
    await session_options(ui, browser_app)
    await expect(
        ui.get_by_role("button", name="File approvals: On", exact=True)
    ).to_be_visible()


async def test_model_selection_survives_late_status_and_uses_configured_default(
    browser_app, ui
):
    from validation.scenarios.support.browser_fullstack import setting
    from validation.scenarios.support.chat_idle_provider import idle_provider
    from validation.scenarios.support.session_ops_chat import configure_provider

    with idle_provider() as provider:
        configure_provider(browser_app.app, provider)
        response = browser_app.app.request(
            "/api/system/models/session-ops-model",
            method="PUT",
            data={
                "provider": "session-ops-provider",
                "model_string": "session-ops-model",
                "capabilities": ["text"],
                "alternate_aliases": ["browser-default-alias"],
            },
        )
        assert response.status_code == 200, response.text
        setting(browser_app.app, "default_model", "browser-default-alias")
        response = browser_app.app.request(
            "/api/system/models/browser-alternate",
            method="PUT",
            data={
                "provider": "session-ops-provider",
                "model_string": "alternate",
                "capabilities": ["text"],
            },
        )
        assert response.status_code == 200, response.text
        async with hold_delivery(
            ui, browser_app.app, browser_app.url + "/api/status"
        ) as gate:
            dialog = await session_options(ui, browser_app)
            await asyncio.wait_for(gate.held.wait(), 10)
            await ui.locator("#model-selector").select_option("browser-alternate")
            gate.release.set()
        await expect(ui.locator("#model-selector")).to_have_value("browser-alternate")
        await dialog.get_by_role(
            "button", name="Close chat settings", exact=True
        ).click()
        await ui.reload()
        await session_options(ui, browser_app)
        # A fresh page resolves the explicit application default rather than
        # silently selecting the first available model in the catalog.
        await expect(ui.locator("#model-selector")).to_have_value("session-ops-model")


@pytest.mark.parametrize("refresh_fails", [False, True])
async def test_real_session_upgrade_preserves_history_and_reports_refresh_outcome(
    browser_app, ui, refresh_fails
):
    from contextlib import asynccontextmanager

    from core.runtime.state import get_runtime_context
    from validation.scenarios.support.browser_fullstack import setting
    from validation.scenarios.support.context_upgrade_recovery_contracts import (
        seed_upgrade_input,
    )
    from validation.scenarios.support.map_author_transport import map_author
    from validation.scenarios.support.session_map_author_http import configure_author

    store = get_runtime_context().chat_store
    raw = seed_upgrade_input(store, browser_app.vault.name, "upgrade-browser")
    with map_author() as provider:
        # The remote map-author LLM alone is controlled. The real upgrade task
        # retrieves canonical history, validates citations and persists the map.
        configure_author(browser_app.app, provider)
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("default_model", "test"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "250"),
            ("compaction_retained_turns", "2"),
            ("compaction_high_watermark_tokens", "500"),
            ("compaction_type", "auto"),
        ):
            setting(browser_app.app, key, value)
        dialog = await session_list(ui, browser_app)

        @asynccontextmanager
        async def refresh_boundary():
            if refresh_fails:
                async with lose_connection(
                    ui,
                    browser_app.app,
                    browser_app.url + "/api/chat/sessions?vault_name=*",
                ):
                    yield
            else:
                yield

        alerts = []

        async def handle_dialog(event):
            if event.type == "alert":
                alerts.append(event.message)
            await event.accept()

        ui.on("dialog", handle_dialog)
        async with refresh_boundary():
            await dialog.locator(
                '[data-session-browser-row-id="upgrade-browser"]'
            ).get_by_role("button", name="Upgrade to Compaction v2", exact=True).click()
            async with asyncio.timeout(30):
                while not alerts:
                    await asyncio.sleep(0.1)
        assert (
            "upgraded" in alerts[0].lower()
            and "failed to upgrade" not in alerts[0].lower()
        )
        assert ("could not refresh" in alerts[0]) == refresh_fails
        assert provider.payloads
        assert (
            store.get_latest_context_checkpoint(
                "upgrade-browser", browser_app.vault.name
            ).checkpoint_kind
            == "session_map"
        )
        assert (
            store.get_history("upgrade-browser", browser_app.vault.name, mode="raw")
            == raw
        )


async def test_loaded_markdown_preserves_user_lines_code_and_assistant_copy_source(
    browser_app, ui
):
    source = "## Answer\n\n**Emphasis** with [source](Welcome.md)."
    thread(
        browser_app,
        "markdown",
        [("First line\nSecond line\n\n    [[not-a-link]]", source)],
    )
    await load_thread(ui, browser_app, "markdown")
    messages = ui.locator("#chat-messages")
    paragraph = messages.locator("p").filter(has_text="First line").first
    assert await paragraph.inner_text() == "First line\nSecond line"
    code = messages.locator("pre code").filter(has_text="[[not-a-link]]")
    await expect(code).to_have_count(1)
    await expect(code.locator("a")).to_have_count(0)
    await expect(
        messages.get_by_role("link", name="source", exact=True)
    ).to_be_visible()
    await ui.context.grant_permissions(["clipboard-read", "clipboard-write"])
    await messages.get_by_role(
        "button", name="Copy to clipboard", exact=True
    ).last.click()
    assert await ui.evaluate("navigator.clipboard.readText()") == source


async def test_late_compaction_pressure_cannot_replace_current_session(browser_app, ui):
    from core.runtime.state import get_runtime_context
    from validation.scenarios.support.browser_fullstack import setting

    seed_discovery_sessions(get_runtime_context().chat_store, browser_app.vault.name)
    for key, value in (
        ("compaction_strategy", "session_map"),
        ("compaction_low_watermark_tokens", "250"),
        ("compaction_high_watermark_tokens", "500"),
        ("default_model", "test"),
    ):
        setting(browser_app.app, key, value)
    async with hold_delivery(
        ui, browser_app.app, "**/api/chat/sessions/long/compaction-status?*"
    ) as gate:
        await load_thread(ui, browser_app, "long")
        await asyncio.wait_for(gate.held.wait(), 10)
        await load_thread(ui, browser_app, "short")
        status = browser_app.app.request(
            "/api/chat/sessions/short/compaction-status",
            params={"vault_name": browser_app.vault.name},
        ).json()
        threshold = status["compaction_high_watermark_tokens"]
        percent = round(status["estimated_tokens_before"] / threshold * 100)
        await expect(ui.locator("#chat-compaction-track")).to_have_attribute(
            "title", re.compile(rf"^{percent}%")
        )
        gate.release.set()
    await expect(ui.locator("#chat-compaction-track")).to_have_attribute(
        "title", re.compile(rf"^{percent}%")
    )
