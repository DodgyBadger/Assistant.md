"""Dashboard renders real tasks, polls while visible and retains last known state."""

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_fullstack import lose_connection, setting
from validation.scenarios.support.chat_idle_provider import idle_provider
from validation.scenarios.support.session_ops_chat import configure_provider

pytestmark = pytest.mark.scenario


async def test_dashboard_polling_preserves_tasks_on_error_and_stops_when_hidden(
    browser_app, ui
):
    import asyncio

    # Only the external model is replaced, making a real task remain running
    # without credentials or nondeterministic model latency.
    with idle_provider() as provider:
        configure_provider(browser_app.app, provider)
        setting(browser_app.app, "default_model", "session-ops-model")
        await ui.goto(browser_app.url)
        await ui.locator("#vault-selector").select_option(browser_app.vault.name)
        await ui.locator("#chat-input").fill(
            "Keep a task running for dashboard observation"
        )
        await ui.get_by_role("button", name="Send message", exact=True).click()
        assert await asyncio.to_thread(provider.started.wait, 15)
        await ui.get_by_role("button", name="Dashboard", exact=True).click()
        tasks = ui.locator("#dashboard-execution-tasks-status")
        await expect(tasks).to_contain_text("1 active task")
        await expect(
            ui.get_by_role("button", name="Stop all tasks", exact=True)
        ).to_be_visible()
        async with lose_connection(
            ui, browser_app.app, browser_app.url + "/api/tasks?include_terminal=false"
        ) as dropped:
            await expect(tasks.get_by_role("status")).to_contain_text(
                "Last known task state is shown", timeout=10000
            )
            await expect(tasks).to_contain_text("1 active task")
            assert dropped
            await ui.get_by_role("button", name="Chat", exact=True).click()
            # A DOM navigation event starts the quiet interval. Wait beyond one
            # documented 2s poll period to prove the invisible view stops polling.
            count = len(dropped)
            await asyncio.sleep(2.3)
            assert len(dropped) == count
        provider.release.set()
        await expect(
            ui.locator("#chat-messages").get_by_text("idle response", exact=True)
        ).to_be_visible(timeout=20000)
        await ui.get_by_role("button", name="Dashboard", exact=True).click()
        await expect(tasks).to_contain_text("No tasks are currently running")


async def test_late_task_poll_cannot_restore_a_task_after_newer_terminal_poll(
    browser_app, ui
):
    import asyncio

    from validation.scenarios.support.browser_fullstack import hold_delivery

    with idle_provider() as provider:
        configure_provider(browser_app.app, provider)
        setting(browser_app.app, "default_model", "session-ops-model")
        await ui.goto(browser_app.url)
        await ui.locator("#vault-selector").select_option(browser_app.vault.name)
        await ui.locator("#chat-input").fill("A held real task")
        await ui.get_by_role("button", name="Send message", exact=True).click()
        assert await asyncio.to_thread(provider.started.wait, 15)
        async with hold_delivery(
            ui,
            browser_app.app,
            browser_app.url + "/api/tasks?include_terminal=false",
            first_only=True,
        ) as gate:
            await ui.get_by_role("button", name="Dashboard", exact=True).click()
            await asyncio.wait_for(gate.held.wait(), 10)
            provider.release.set()
            await expect(
                ui.locator("#chat-messages").get_by_text("idle response", exact=True)
            ).to_be_attached(timeout=20000)
            tasks = ui.locator("#dashboard-execution-tasks-status")
            await expect(tasks).to_contain_text(
                "No tasks are currently running", timeout=10000
            )
            gate.release.set()
        await expect(tasks).to_contain_text("No tasks are currently running")
        await expect(
            ui.get_by_role("button", name="Stop all tasks", exact=True)
        ).to_have_count(0)
