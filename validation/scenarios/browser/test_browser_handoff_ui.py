"""Human sign-in through the real chat browser modal with private evidence."""

import asyncio
import json
from urllib.parse import urlsplit

import pytest
from playwright.async_api import expect

from validation.support.browser.browser_fullstack import hold_delivery, setting
from validation.support.browser.handoff_provider import browser_handoff_provider
from validation.support.browser.private_page import private_browser_page
from validation.support.browser.retained_site import retained_browser_site
from validation.support.chat.chat_persistence import PERSISTENCE_CONTEXT

pytestmark = pytest.mark.scenario


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    # Use the installed full Chromium bundle, as production does. The optional
    # separate headless-shell distribution is not installed in this container.
    return {**browser_type_launch_args, "channel": "chromium", "chromium_sandbox": True}


@pytest.mark.parametrize("finish", ["return", "stop", "detach"])
async def test_desktop_handoff_can_close_reopen_sign_in_and_resume_same_chat_task(
    browser_app, browser, monkeypatch, finish
):
    app = browser_app.app
    app.write(
        browser_app.vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT
    )
    setting(app, "browser_min_memory_headroom_mb", "0")
    setting(app, "compaction_type", "none")
    sentinel = "synthetic-ui-private-password-62941"
    app.evidence.register_secret(sentinel)
    with retained_browser_site(app, monkeypatch) as site:
        site.sign_in_on_submit = True
        site.pages["/login"] = (
            b'<style>body{margin:0}input{position:absolute;left:20px;top:20px;width:200px;height:40px}</style><form method="post" action="/submit"><input type="password" name="password"></form>'
        )
        with browser_handoff_provider(app, site) as calls:
            setting(app, "default_model", "code-tool-model")
            async with private_browser_page(
                browser_app, browser, forbidden=(sentinel,)
            ) as page:
                await page.goto(browser_app.url)
                await page.locator("#vault-selector").select_option(
                    browser_app.vault.name
                )
                await page.locator("#chat-input").fill(
                    "Open the controlled website, request human sign-in, then report completion."
                )
                await page.get_by_role(
                    "button", name="Send message", exact=True
                ).click()
                link = page.get_by_role("button", name="Open browser", exact=True)
                await expect(link).to_be_visible(timeout=20000)
                async with page.expect_response(
                    lambda r: r.url.endswith("/claim")
                ) as claimed:
                    await link.click()
                claim_response = await claimed.value
                assert claim_response.status == 200
                claimed_view = await claim_response.json()
                session_id = claim_response.url.split("/sessions/", 1)[1].split("/", 1)[
                    0
                ]
                modal = page.get_by_role("dialog", name="Browser", exact=True)
                await expect(modal).to_be_visible()
                preview = modal.get_by_role("img", name="Browser page", exact=True)
                await expect(preview).to_be_visible()
                site_url = urlsplit(site.url("/login"))
                await expect(modal.get_by_label("Website origin")).to_have_text(
                    f"{site_url.scheme}://{site_url.netloc}"
                )
                handback = modal.get_by_role(
                    "button", name="Return control", exact=True
                )
                await expect(handback).to_be_enabled()
                bounds = await preview.bounding_box()
                assert bounds is not None
                # Hold only delivery of a real passive frame. A user's click
                # on the still-displayed page must not disappear during polling.
                async with hold_delivery(
                    page,
                    app,
                    browser_app.url
                    + f"/api/chat/sessions/{session_id}/browser-handoff/{claimed_view['handoff_id']}/frame",
                    first_only=True,
                ) as gate:
                    await asyncio.wait_for(gate.held.wait(), 5)
                    await preview.click(
                        position={
                            "x": 50 * bounds["width"] / 1280,
                            "y": 40 * bounds["height"] / 800,
                        }
                    )
                    await modal.get_by_label(
                        "Browser keyboard input"
                    ).press_sequentially(sentinel)
                    gate.release.set()
                await expect(handback).to_be_enabled()
                async with page.expect_response(
                    lambda r: "/browser-handoff/" in r.url and r.url.endswith("/return")
                ) as refused:
                    await handback.click()
                assert (await refused.value).status == 409
                await expect(modal).to_be_visible()
                local_state = await page.evaluate(
                    "JSON.stringify([localStorage, sessionStorage])"
                )
                private_stored = sentinel in local_state
                assert not private_stored, "Private input reached browser storage"
                blob_url = await preview.get_attribute("src")
                if finish == "detach":
                    async with hold_delivery(
                        page,
                        app,
                        browser_app.url
                        + f"/api/chat/sessions/{session_id}/browser-handoff/{claimed_view['handoff_id']}/frame",
                        first_only=True,
                    ) as gate:
                        await asyncio.wait_for(gate.held.wait(), 5)
                        # Real DOM teardown above the message's direct parent,
                        # while an unchanged server frame is still in delivery.
                        await page.locator("#chat-messages").evaluate(
                            "element => element.parentElement.remove()"
                        )
                        await expect(modal).to_have_count(0)
                        gate.release.set()
                    assert not await page.evaluate(
                        "async url => { try { await fetch(url); return true; } catch { return false; } }",
                        blob_url,
                    )
                    state = await app.request_async(
                        f"/api/chat/sessions/{session_id}/browser-handoff"
                    )
                    assert state.json()["status"] == "human"
                    assert len(calls) == 2 and site.submissions == []
                    return
                await modal.get_by_role("button", name="Close", exact=True).click()
                await expect(modal).to_have_count(0)
                assert not await page.evaluate(
                    "async url => { try { await fetch(url); return true; } catch { return false; } }",
                    blob_url,
                )
                assert len(calls) == 2 and site.submissions == []
                if finish == "stop":
                    await page.get_by_role(
                        "button", name="Stop the active response", exact=True
                    ).click()
                    task = await app.wait_task(claimed_view["task_id"])
                    assert task["status"] == "cancelled"
                    await expect(link).to_be_disabled()
                    await expect(modal).to_have_count(0)
                    assert len(calls) == 2 and site.submissions == []
                    return
                # Reload while human control is held: replay restores the link,
                # and reopening reclaims only the same current generation.
                await page.reload()
                await page.locator("#vault-selector").select_option(
                    browser_app.vault.name
                )
                await page.get_by_role(
                    "button", name="Open chat settings", exact=True
                ).click()
                await page.locator("[data-session-browser-row-id]").first.click()
                await expect(link).to_be_enabled()
                await link.click()
                modal = page.get_by_role("dialog", name="Browser", exact=True)
                await expect(modal).to_be_visible()
                handback = modal.get_by_role(
                    "button", name="Return control", exact=True
                )
                await expect(handback).to_be_enabled()
                await modal.get_by_label("Browser keyboard input").press("Enter")
                async with asyncio.timeout(10):
                    while not site.submissions:
                        await asyncio.sleep(0.05)
                await expect(handback).to_be_enabled()
                await handback.click()
                await expect(modal).to_have_count(0)
                await expect(
                    page.locator("#chat-messages").get_by_text(
                        "Human handoff completed.", exact=True
                    )
                ).to_be_visible(timeout=20000)
                assert site.submissions == [{"password": sentinel}]
                assert len(calls) == 3
                from core.runtime.state import get_runtime_context

                runtime = get_runtime_context()
                task = await app.wait_task(claimed_view["task_id"])
                assert task["status"] == "completed"
                # Provider/history audit is in memory, before evidence redaction.
                private_leaked = (
                    sentinel in json.dumps(calls)
                    or sentinel
                    in repr(
                        runtime.chat_store.get_stored_messages(
                            session_id, browser_app.vault.name
                        )
                    )
                    or sentinel
                    in repr(
                        runtime.chat_store.get_tool_events(
                            session_id, browser_app.vault.name
                        )
                    )
                )
                assert not private_leaked
                await page.reload()
                await page.locator("#vault-selector").select_option(
                    browser_app.vault.name
                )
                await page.get_by_role(
                    "button", name="Open chat settings", exact=True
                ).click()
                await page.locator("[data-session-browser-row-id]").first.click()
                await expect(
                    page.locator("#chat-messages").get_by_text(
                        "Human handoff completed.", exact=True
                    )
                ).to_be_visible()
                await expect(
                    page.get_by_role("button", name="Open browser", exact=True)
                ).to_have_count(0)
