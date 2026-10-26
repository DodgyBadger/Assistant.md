"""Bookmark workflows through the shared browser surfaces with deterministic APIs."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=[1280, 390], ids=["desktop", "mobile"])
def favorites_page(request: pytest.FixtureRequest) -> Iterator[Page]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--disable-gpu"])
        page = browser.new_page(viewport={"width": request.param, "height": 900})
        page.set_content(
            '<select id="vault"><option>TestVault</option></select><div id="chat"></div>'
        )
        page.add_style_tag(path=str(_ROOT / "static/app.css"))
        page.add_style_tag(
            content=".hidden,[hidden]{display:none!important}.fixed{position:fixed}.inset-0{inset:0}.relative{position:relative}.flex{display:flex}.flex-col{flex-direction:column}.min-h-0{min-height:0}.flex-1{flex:1}.overflow-y-auto{overflow-y:auto}"
        )
        for name in (
            "icons",
            "utils",
            "vault-favorites",
            "vault-explorer-state",
            "vault-explorer-toolbar",
            "vault-explorer-destination",
            "vault-explorer-import-options",
            "vault-explorer-imports",
            "vault-explorer-actions",
            "vault-explorer-batch-moves",
            "vault-explorer-search",
            "vault-explorer-controller",
            "vault-path-picker",
            "file-reference-links",
            "vault-selection-prompt",
            "file-references",
        ):
            page.add_script_tag(path=str(_ROOT / f"static/js/{name}.js"))
        page.evaluate("""() => {
            window.errors = [];
            window.addEventListener('error', event => errors.push(event.message));
            window.state = { sessions: [
                {session_id:'newer', title:'Recent conversation', last_activity_at:'2026-10-26 12:00:00', favorite:false},
                {session_id:'older', title:'Bookmarked conversation', last_activity_at:'2026-10-25 12:00:00', favorite:true, workspace:{path:'Projects/Long workspace name'}},
            ]};
            window.elements = {vaultSelector:document.querySelector('#vault'), chatMessages:document.querySelector('#chat')};
            window.favorites = new Set(['b.md']);
            window.requests = [];
            const items = [
                {path:'Folder',name:'Folder',kind:'directory',has_children:true},
                {path:'a.md',name:'a.md',kind:'file'},
                {path:'b.md',name:'b.md',kind:'file'},
                {path:'Folder/child.md',name:'child.md',kind:'file'},
            ];
            window.fetch = async (url, options={}) => {
                requests.push({url, options});
                const parsed = new URL(url, 'http://fixture/');
                const params = parsed.searchParams;
                const data = options.body ? JSON.parse(options.body) : {};
                let payload;
                if (url.includes('/chat/sessions/')) {
                    payload = {favorite:data.favorite};
                } else if (url.includes('/favorites')) {
                    if (options.method === 'PATCH') for (const path of data.paths) {
                        if (data.favorite) favorites.add(path); else favorites.delete(path);
                    }
                    payload = {paths:[...favorites]};
                } else if (url.includes('/content-search')) {
                    payload = {matches:items.filter(item => item.kind === 'file' && (!params.has('favorites_only') || favorites.has(item.path)))
                        .map(item => ({path:item.path, line:1, snippet:'needle match'}))};
                } else if (url.includes('/file-refs')) {
                    const path = params.get('path') || '';
                    const query = params.get('query') || '';
                    payload = {items:items.filter(item => {
                        if (params.has('favorites_only')) return favorites.has(item.path) && (!path || item.path.startsWith(path+'/')) && item.name.includes(query);
                        if (query) return item.name.includes(query);
                        return item.path.split('/').slice(0,-1).join('/') === path;
                    }).map(item => ({...item, favorite:favorites.has(item.path)}))};
                } else if (url.includes('/files?')) {
                    payload = {content:'# Example file',sha256:'hash',favorite:favorites.has(params.get('path'))};
                } else throw new Error(`Unexpected request: ${url}`);
                return {ok:true, json:async()=>payload};
            };
            window.picker = VaultPathPicker.create({elements,icons:AssistantMDIcons,utils:AssistantMDUtils});
            window.files = FileReferences.create({state,elements,icons:AssistantMDIcons,utils:AssistantMDUtils,
                callbacks:{openPathPicker:options=>picker.open(options),closePathPicker:()=>picker.close(),
                    renderMarkdownPreview:(target,text)=>{target.textContent=text;}}});
        }""")
        yield page
        assert page.evaluate("errors") == []
        browser.close()


def test_file_and_folder_selection_bookmarks_and_filtered_search(
    favorites_page: Page,
) -> None:
    page = favorites_page
    page.evaluate("files.openExplorer()")
    expect(
        page.locator("[data-vault-path-picker-results] [data-vault-path-picker-row]")
    ).to_have_count(3)
    # A mixed selection adds every path, including a folder, with one click.
    for path in ("Folder", "a.md", "b.md"):
        page.locator(f'[data-vault-explorer-select-item][data-path="{path}"]').check()
    toggle = page.locator('[data-vault-explorer-toolbar-action="favorite"]')
    expect(toggle).to_have_attribute("aria-pressed", "false")
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    assert page.evaluate("[...favorites].sort()") == ["Folder", "a.md", "b.md"]
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    assert page.evaluate("[...favorites]") == []
    toggle.click()
    expect(toggle).to_have_attribute("aria-pressed", "true")
    page.locator("[data-vault-favorites-filter]").click()
    expect(page.locator("[data-vault-favorites-filter]")).to_have_attribute(
        "aria-pressed", "true"
    )
    query = page.locator("[data-vault-path-picker-query]")
    query.fill("a.md")
    expect(
        page.locator("[data-vault-path-picker-results] [data-vault-path-picker-row]")
    ).to_have_count(1)
    page.locator("[data-vault-explorer-search-mode-toggle]").click()
    page.get_by_role("menuitemradio", name="Contents", exact=True).click()
    query.fill("needle")
    expect(
        page.locator("[data-vault-path-picker-results] [data-vault-path-picker-row]")
    ).to_have_count(2)
    assert page.evaluate(
        "requests.filter(r=>r.url.includes('/content-search')).at(-1).url.includes('favorites_only=true')"
    )
    page.locator('[data-vault-path-picker-select="a.md"]').click()
    bookmark = page.locator("[data-vault-file-favorite]")
    expect(bookmark).to_have_attribute("aria-pressed", "true")
    bookmark.click()
    expect(bookmark).to_have_attribute("aria-pressed", "false")
    page.locator("[data-vault-file-back]").click()
    expect(
        page.locator("[data-vault-path-picker-results] [data-vault-path-picker-row]")
    ).to_have_count(1)
    assert page.evaluate("[...favorites].sort()") == ["Folder", "b.md"]
    page.locator("[data-vault-explorer-search-mode-toggle]").click()
    page.get_by_role("menuitemradio", name="Files", exact=True).click()
    query.fill("")
    expect(page.locator('[data-vault-path-picker-select="Folder"]')).to_be_visible()
    # Opening the bookmarked folder navigates its ordinary contents.
    page.locator('[data-vault-path-picker-select="Folder"]').click()
    expect(page.locator("[data-vault-favorites-filter]")).to_have_attribute(
        "aria-pressed", "false"
    )
    expect(
        page.locator('[data-vault-path-picker-select="Folder/child.md"]')
    ).to_be_visible()


def test_pending_or_failed_bookmark_keeps_selection_control_usable(
    favorites_page: Page,
) -> None:
    page = favorites_page
    page.evaluate("files.openExplorer()")
    page.locator('[data-vault-explorer-select-item][data-path="a.md"]').check()
    page.evaluate("""() => {
        const original = fetch;
        window.fetch = (url, options={}) => options.method === 'PATCH'
            ? new Promise(resolve => { window.finishBookmark = resolve; })
            : original(url, options);
    }""")
    toggle = page.locator('[data-vault-explorer-toolbar-action="favorite"]')
    toggle.click()
    expect(toggle).to_be_visible()
    expect(toggle).to_be_disabled()
    page.evaluate(
        "finishBookmark({ok:false,json:async()=>({message:'Bookmark update failed'})})"
    )
    expect(toggle).to_be_enabled()
    expect(toggle).to_have_attribute("aria-pressed", "false")
    expect(page.locator("[data-vault-path-picker-status]")).to_contain_text(
        "Bookmark update failed"
    )
    assert page.evaluate("[...favorites]") == ["b.md"]
