"""Chat bookmark controls and cards resume the same existing conversation."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=[1280, 390], ids=["desktop", "mobile"])
def chat_page(request: pytest.FixtureRequest) -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        page = browser.new_page(viewport={"width": request.param, "height": 900})
        page.set_content(
            '<select id="vault"><option>TestVault</option><option>OtherVault</option></select><div id="chat"></div>'
        )
        page.add_style_tag(path=str(_ROOT / "static/app.css"))
        page.add_style_tag(
            content=".hidden,[hidden]{display:none!important}.fixed{position:fixed}.inset-0{inset:0}.relative{position:relative}.flex{display:flex}.flex-col{flex-direction:column}.min-h-0{min-height:0}.flex-1{flex:1}.overflow-y-auto{overflow-y:auto}"
        )
        for name in ("icons", "utils", "session-controls", "chat-start-panel"):
            page.add_script_tag(path=str(_ROOT / f"static/js/{name}.js"))
        page.evaluate("""() => {
            window.errors = [];
            window.addEventListener('error', event => errors.push(event.message));
            window.state = { sessions: [
                {session_id:'newer', title:'Recent conversation', last_activity_at:'2026-10-26 12:00:00', favorite:false},
                {session_id:'older', title:'Bookmarked conversation', last_activity_at:'2026-10-25 12:00:00', favorite:true, workspace:{path:'Projects/Long workspace name'}},
            ]};
            window.elements = {vaultSelector:document.querySelector('#vault'), chatMessages:document.querySelector('#chat')};
            window.fetch = async (url, options={}) => ({ok:true,json:async()=>({favorite:JSON.parse(options.body).favorite})});
            window.opened = [];
            window.start = ChatStartPanel.create({state,elements,icons:AssistantMDIcons,utils:AssistantMDUtils,
                callbacks:{selectSession:id=>sessions.selectSession(id)}});
            window.renderStart = () => { elements.chatMessages.innerHTML=''; start.render(elements.chatMessages); };
            window.sessions = SessionControls.create({state,elements,icons:AssistantMDIcons,utils:AssistantMDUtils,
                callbacks:{loadSession:id=>opened.push(id),refreshEmptyState:renderStart}});
            renderStart();
        }""")
        yield page
        assert page.evaluate("errors") == []
        browser.close()


def test_chat_bookmarks_open_existing_sessions_and_refresh_cards(
    chat_page: Page,
) -> None:
    page = chat_page
    expect(page.locator("[data-chat-favorite]")).to_have_count(1)
    page.locator('[data-chat-favorite="older"]').click()
    assert page.evaluate("opened") == ["older"]
    page.evaluate("sessions.openSessionBrowserModal()")
    page.locator(
        '[data-session-action="favorite"][data-session-action-id="newer"]'
    ).click()
    expect(
        page.locator('[data-session-action="favorite"][data-session-action-id="newer"]')
    ).to_have_attribute("aria-pressed", "true")
    assert page.locator("[data-session-browser-row-id]").evaluate_all(
        "rows=>rows.map(row=>row.dataset.sessionBrowserRowId)"
    ) == ["newer", "older"]
    page.locator(
        '[data-session-action="favorite"][data-session-action-id="older"]'
    ).click()
    page.get_by_role("button", name="Close chat settings").click()
    expect(page.locator("[data-chat-favorite]")).to_have_count(1)
    expect(page.locator('[data-chat-favorite="newer"]')).to_be_visible()
    page.evaluate("state.isLoading=true;renderStart()")
    expect(page.locator("[data-chat-favorite]")).to_be_disabled()
    page.evaluate("state.isLoading=false;renderStart()")
    page.locator("[data-chat-favorite]").focus()
    page.keyboard.press("Enter")
    assert page.evaluate("opened") == ["older", "newer"]


def test_late_bookmark_response_cannot_modify_another_vault(chat_page: Page) -> None:
    page = chat_page
    page.evaluate("""() => {
        window.fetch = () => new Promise(resolve => { window.finishFavorite = resolve; });
        sessions.openSessionBrowserModal();
    }""")
    page.locator(
        '[data-session-action="favorite"][data-session-action-id="newer"]'
    ).click()
    page.wait_for_function("typeof finishFavorite === 'function'")
    page.evaluate("""() => {
        elements.vaultSelector.value = 'OtherVault';
        state.sessions = [{session_id:'newer',title:'Other vault',favorite:false}];
        sessions.renderSelector();
        finishFavorite({ok:true,json:async()=>({favorite:true})});
    }""")
    expect(
        page.locator('[data-session-action="favorite"][data-session-action-id="newer"]')
    ).to_have_attribute("aria-pressed", "false")
    page.get_by_role("button", name="Close chat settings").click()
    expect(page.locator("[data-chat-favorite]")).to_have_count(0)
