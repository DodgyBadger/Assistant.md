"""Human sign-in through the real chat browser modal with private evidence."""

import asyncio
import json
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import expect

from validation.support.browser.browser_fullstack import hold_delivery, setting
from validation.support.browser.handoff_provider import browser_handoff_provider
from validation.support.browser.private_page import private_browser_page
from validation.support.browser.retained_site import retained_browser_site
from validation.support.browser.reverse_proxy import proxied_browser_application
from validation.support.chat.chat_persistence import PERSISTENCE_CONTEXT

pytestmark = pytest.mark.scenario


async def return_after_passive_frame(page, app, modal, handback, frame_url, status):
    async with hold_delivery(page, app, frame_url, first_only=True) as gate:
        await asyncio.wait_for(gate.held.wait(), 5)
        sent_after_capture = []

        def observe(request):
            if "/browser-handoff/" in request.url and request.url.endswith("/return"):
                sent_after_capture.append(gate.release.is_set())

        page.on("request", observe)
        try:
            async with page.expect_response(
                lambda r: "/browser-handoff/" in r.url and r.url.endswith("/return")
            ) as returned:
                await expect(handback).to_be_enabled(timeout=1500)
                await handback.click()
                await expect(handback).to_be_disabled()
                await expect(
                    modal.get_by_label("Browser keyboard input")
                ).to_have_js_property("readOnly", True)
                assert sent_after_capture == []
                gate.release.set()
            response = await returned.value
            assert response.status == status
            if status == 409:
                assert (await response.json())["detail"] == "credentials_pending"
            assert sent_after_capture == [
                True
            ], "Handback overlapped capture or was repeated"
        finally:
            page.remove_listener("request", observe)


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    # Use the installed full Chromium bundle, as production does. The optional
    # separate headless-shell distribution is not installed in this container.
    return {**browser_type_launch_args, "channel": "chromium", "chromium_sandbox": True}


@pytest_asyncio.fixture(params=["direct", "proxy"])
async def handoff_browser_app(browser_app, request):
    if request.param == "proxy":
        async with proxied_browser_application(browser_app) as application:
            yield application
    else:
        yield browser_app


@pytest.mark.parametrize("finish", ["return", "stop", "detach"])
async def test_desktop_handoff_can_close_reopen_sign_in_and_resume_same_chat_task(
    handoff_browser_app, browser, monkeypatch, finish
):
    browser_app = handoff_browser_app
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
                returns = []
                page.on(
                    "request",
                    lambda request: (
                        returns.append(True)
                        if "/browser-handoff/" in request.url
                        and request.url.endswith("/return")
                        else None
                    ),
                )
                if finish == "return":
                    await page.set_viewport_size({"width": 900, "height": 620})
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
                if finish == "return":
                    # Native scrolling over app chrome must not become a wheel
                    # event inside the remote website or reset on frame refresh.
                    await modal.get_by_role(
                        "heading", name="Browser", exact=True
                    ).hover()
                    await page.mouse.wheel(0, 1000)
                    await page.wait_for_function(
                        "() => document.querySelector('dialog[aria-label=Browser]').scrollTop > 0"
                    )
                    before = await modal.evaluate("element => element.scrollTop")
                    for _ in range(2):
                        async with page.expect_response(
                            lambda r: "/browser-handoff/" in r.url
                            and r.url.endswith("/frame")
                        ) as captured:
                            pass
                        await (await captured.value).finished()
                    await expect(preview).to_be_visible()
                    after = await modal.evaluate("element => element.scrollTop")
                    assert (
                        after >= before - 1
                    ), "Passive frame refresh reset modal scrolling"
                    await expect(handback).to_be_in_viewport(ratio=1)
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
                frame_url = (
                    browser_app.url
                    + f"/api/chat/sessions/{session_id}/browser-handoff/{claimed_view['handoff_id']}/frame"
                )
                await return_after_passive_frame(
                    page, app, modal, handback, frame_url, 409
                )
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
                        await expect(handback).to_be_enabled()
                        await handback.click()
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
                    assert len(returns) == 1
                    return
                if finish == "stop":
                    async with hold_delivery(
                        page, app, frame_url, first_only=True
                    ) as gate:
                        await asyncio.wait_for(gate.held.wait(), 5)
                        await expect(handback).to_be_enabled()
                        await handback.click()
                        await modal.get_by_role(
                            "button", name="Close", exact=True
                        ).click()
                        gate.release.set()
                else:
                    await modal.get_by_role("button", name="Close", exact=True).click()
                await expect(modal).to_have_count(0)
                assert not await page.evaluate(
                    "async url => { try { await fetch(url); return true; } catch { return false; } }",
                    blob_url,
                )
                assert len(calls) == 2 and site.submissions == []
                assert len(returns) == 1
                if finish == "stop":
                    await page.get_by_role(
                        "button", name="Stop the active response", exact=True
                    ).click()
                    task = await app.wait_task(claimed_view["task_id"])
                    assert task["status"] == "cancelled"
                    await expect(link).to_be_disabled()
                    await expect(modal).to_have_count(0)
                    assert len(calls) == 2 and site.submissions == []
                    assert len(returns) == 1
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
                await return_after_passive_frame(
                    page, app, modal, handback, frame_url, 200
                )
                await expect(modal).to_have_count(0)
                await expect(
                    page.locator("#chat-messages").get_by_text(
                        "Human handoff completed.", exact=True
                    )
                ).to_be_visible(timeout=20000)
                assert site.submissions == [{"password": sentinel}]
                assert len(calls) == 3
                assert len(returns) == 2
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


async def test_sign_in_window_uses_same_modal_and_restores_original_chat_browser(
    browser_app, browser, monkeypatch
):
    app = browser_app.app
    setting(app, "browser_min_memory_headroom_mb", "0")
    setting(app, "compaction_type", "none")
    sentinel = "synthetic-ui-popup-password-83015"
    app.evidence.register_secret(sentinel)
    with retained_browser_site(app, monkeypatch) as site:
        site.sign_in_on_submit = True
        site.pages["/login"] = (
            b"""<style>body{margin:0}button{position:absolute;left:20px;top:20px;width:180px;height:40px}</style><button onclick="window.open('/popup-login','sign-in')">Sign in</button>"""
        )
        site.pages["/popup-login"] = (
            b'<style>body{margin:0}input{position:absolute;left:20px;top:20px;width:200px;height:40px}</style><form method="post" action="/submit"><input type="password" name="password"></form>'
        )
        site.submission_body = (
            b'<script>opener.location.href="/after";window.close()</script>'
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
                await page.get_by_role("button", name="Open browser", exact=True).click(
                    timeout=20000
                )
                modal = page.get_by_role("dialog", name="Browser", exact=True)
                handback = modal.get_by_role(
                    "button", name="Return control", exact=True
                )

                async def click_page():
                    await expect(handback).to_be_enabled()
                    preview = modal.get_by_role("img", name="Browser page", exact=True)
                    bounds = await preview.bounding_box()
                    assert bounds is not None
                    await preview.click(
                        position={
                            "x": 50 * bounds["width"] / 1280,
                            "y": 40 * bounds["height"] / 800,
                        }
                    )

                await click_page()
                original = modal.get_by_role("button", name="Original page", exact=True)
                sign_in = modal.get_by_role("button", name="Sign-in window", exact=True)
                await expect(original).to_be_enabled(timeout=10000)
                await expect(sign_in).to_be_disabled()
                assert (
                    len(page.context.pages) == 1
                ), "Authentication stays inside the existing app modal"
                await click_page()
                await modal.get_by_label("Browser keyboard input").press_sequentially(
                    sentinel
                )
                await expect(handback).to_be_enabled()
                await original.click()
                await expect(sign_in).to_be_enabled()
                async with page.expect_response(
                    lambda response: response.url.endswith("/return")
                ) as refused:
                    await handback.click()
                assert (await refused.value).status == 409
                assert len(calls) == 2
                await expect(modal).to_be_visible()
                await sign_in.click()
                await expect(original).to_be_enabled()
                await expect(handback).to_be_enabled()
                await modal.get_by_label("Browser keyboard input").press("Enter")
                await expect(original).to_be_hidden(timeout=10000)
                await expect(sign_in).to_be_hidden()
                await expect(handback).to_be_enabled()
                assert site.submissions == [{"password": sentinel}]
                await handback.click()
                await expect(modal).to_have_count(0)
                await expect(
                    page.locator("#chat-messages").get_by_text(
                        "Human handoff completed.", exact=True
                    )
                ).to_be_visible(timeout=20000)
                assert len(calls) == 3
                leaked = sentinel in json.dumps(
                    calls
                ) or sentinel in await page.evaluate(
                    "JSON.stringify([localStorage, sessionStorage])"
                )
                assert not leaked
