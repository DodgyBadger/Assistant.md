"""Frontend smoke tests for session controls."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SESSION_CONTROLS_MODULE = _PROJECT_ROOT / "static/js/session-controls.js"


def test_compaction_pressure_updates_map_only_context_and_rejects_stale_responses() -> (
    None
):
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const classes = new Set();
const fill = { style: {}, classList: {
    add(name) { classes.add(name); },
    remove(...names) { names.forEach(name => classes.delete(name)); },
} };
const elements = { vaultSelector: { value: 'Vault' }, compactionFill: fill, compactionTrack: {} };
const state = { sessionId: 'session', compactionStatusRequestId: 0 };
const controls = SessionControls.create({ state, elements, icons: {}, utils: {}, callbacks: {} });
const status = tokens => ({ compaction_type: 'auto', compaction_high_watermark_tokens: 100,
    estimated_tokens_before: tokens, retained_message_count: 0 });
const response = tokens => ({ ok: true, json: async () => status(tokens) });
(async () => {
    global.fetch = async (_url, options) => { assert.strictEqual(options.cache, 'no-store'); return response(120); };
    await controls.refreshCompactionProgress();
    assert.strictEqual(fill.style.width, '100%');
    assert(classes.has('compaction-hot'));
    global.fetch = async () => response(5);
    await controls.refreshCompactionProgress();
    assert.strictEqual(fill.style.width, '5%');
    assert(!classes.has('compaction-hot'), 'Map-only context must clear the red indicator.');

    for (const change of ['session', 'vault', 'request', 'abort']) {
        state.sessionId = 'session'; elements.vaultSelector.value = 'Vault';
        let resolveJson;
        const observer = new AbortController();
        global.fetch = async () => ({ ok: true, json: () => new Promise(resolve => { resolveJson = resolve; }) });
        const pending = controls.refreshCompactionProgress({ signal: observer.signal });
        await new Promise(resolve => setImmediate(resolve));
        if (change === 'session') state.sessionId = 'other';
        if (change === 'vault') elements.vaultSelector.value = 'Other';
        if (change === 'abort') observer.abort();
        if (change === 'request') {
            global.fetch = async () => response(10);
            await controls.refreshCompactionProgress();
        }
        const currentWidth = fill.style.width;
        resolveJson(status(120));
        await pending;
        assert.strictEqual(fill.style.width, currentWidth, `Stale ${change} response changed pressure.`);
        assert(!classes.has('compaction-hot'));
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(
        ["node", "-e", harness, str(_SESSION_CONTROLS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_session_browser_content_search_lifecycle() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
class Element {
    closest(selector) { return this.matches?.[selector] || null; }
    setAttribute(name, value) { this[name] = value; }
    focus() { this.focused = true; }
}
class HTMLInputElement extends Element { focus() {} select() {} }
class HTMLButtonElement extends Element {}
class HTMLElement extends Element {}
Object.assign(global, { Element, HTMLInputElement, HTMLButtonElement, HTMLElement });
global.window = global;
let modal = null;
let timerId = 0;
const timers = new Map();
window.setTimeout = (fn) => { timers.set(++timerId, fn); return timerId; };
window.clearTimeout = (id) => timers.delete(id);
const list = { innerHTML: '' };
const count = { textContent: '' };
const input = new HTMLInputElement();
input.id = 'session-browser-filter';
input.dataset = {};
const toggle = new HTMLButtonElement();
toggle.matches = {'[data-session-browser-search-mode-toggle]':toggle};
const menu = new HTMLElement();
menu.hidden = true;
const modeOptions = ['name', 'content'].map((value) => {
    const option = new HTMLButtonElement();
    option.dataset = {sessionBrowserSearchModeOption:value};
    option.matches = {'[data-session-browser-search-mode-option]':option,
        '[data-session-browser-search-mode-menu]':menu};
    return option;
});
menu.querySelectorAll = () => modeOptions;
menu.querySelector = () => modeOptions.find((option) => option['aria-checked'] === 'true');
global.document = {
    body: { appendChild(node) { modal = node; } },
    createElement() {
        return {
            listeners: {},
            addEventListener(name, fn) { this.listeners[name] = fn; },
            querySelector(selector) {
                return { '#session-browser-list': list, '#session-browser-count': count,
                    '#session-browser-filter': input,
                    '[data-session-browser-search-mode-menu]':menu,
                    '[data-session-browser-search-mode-toggle]':toggle }[selector] || null;
            },
            remove() { modal = null; },
        };
    },
    getElementById(id) { return id === 'session-browser-modal' ? modal : null; },
    querySelector() { return null; },
};
const requests = [];
global.fetch = (url, options) => new Promise((resolve) => requests.push({url, options, resolve}));
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const state = { sessions: [
    {session_id:'one', title:'First session'}, {session_id:'two', title:'Second session'},
] };
const elements = { vaultSelector: { value: 'Vault' } };
const escapeHtml = (s) => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
const controller = SessionControls.create({ state, elements, icons:{}, utils:{escapeHtml}, sessionMap:{}, callbacks:{} });
const type = (value) => { input.value = value; modal.listeners.input({target:input}); };
const changeMode = (value) => { modal.listeners.click({target:modeOptions.find((option) => option.dataset.sessionBrowserSearchModeOption === value)}); };
const startRequest = () => {
    assert.strictEqual(timers.size, 1);
    const [id, fn] = timers.entries().next().value;
    timers.delete(id);
    return fn();
};
const finish = async (index, session, excerpt = '') => {
    requests[index].resolve({ok:true, json:async () => ({limit:20, matches:[{
        session, evidence:[{source:'transcript', excerpt}], score:1,
    }]})});
};
(async () => {
    controller.openSessionBrowserModal();
    type('First');
    assert(list.innerHTML.includes('First session') && !list.innerHTML.includes('Second session'));
    assert.strictEqual(requests.length, 0, 'Names filtering must stay local');
    await modal.listeners.click({target:toggle});
    assert.strictEqual(menu.hidden, false);
    assert.strictEqual(toggle['aria-expanded'], 'true');
    assert(modeOptions[0].focused);
    await modal.listeners.keydown({target:modeOptions[0], key:'ArrowDown', preventDefault(){}});
    assert(modeOptions[1].focused);
    await modal.listeners.keydown({target:modeOptions[1], key:'Escape', preventDefault(){}, stopPropagation(){}});
    assert.strictEqual(menu.hidden, true);
    assert(modal, 'Escape closes the mode menu without closing the browser');
    assert(toggle.focused);
    changeMode('content');
    assert.strictEqual(toggle.textContent, 'Contents');
    assert.strictEqual(modeOptions[1]['aria-checked'], 'true');
    assert(count.textContent.includes('Searching'));
    const stale = startRequest();
    type('old');
    type('new & topic');
    assert.strictEqual(timers.size, 1, 'Debounce replaces queued searches');
    assert(requests[0].options.signal.aborted);
    const current = startRequest();
    assert.strictEqual(new URL(requests[1].url, 'http://test/').searchParams.get('query'), 'new & topic');
    await finish(1, state.sessions[1], '<script>unsafe</script>');
    await current;
    assert(list.innerHTML.includes('Second session') && !list.innerHTML.includes('First session'));
    assert(list.innerHTML.includes('&lt;script&gt;') && !list.innerHTML.includes('<script>'));
    assert(count.textContent.includes('20'));
    await finish(0, state.sessions[0], 'stale');
    await stale;
    assert(!list.innerHTML.includes('stale'), 'Late responses must not replace newer results');
    type('scope');
    const oldVault = startRequest();
    elements.vaultSelector.value = 'OtherVault';
    controller.renderSelector();
    const newVault = startRequest();
    assert.strictEqual(new URL(requests[3].url, 'http://test/').searchParams.get('vault_name'), 'OtherVault');
    await finish(2, state.sessions[0], 'wrong-vault');
    await oldVault;
    assert(!list.innerHTML.includes('wrong-vault'));
    await finish(3, state.sessions[1], 'correct-vault');
    await newVault;
    type('');
    assert.strictEqual(timers.size, 0);
    assert(list.innerHTML.includes('First session') && list.innerHTML.includes('Second session'));
    type('closing');
    const closed = startRequest();
    modal.listeners.keydown({target:input, key:'Escape'});
    assert(requests[4].options.signal.aborted);
    controller.openSessionBrowserModal();
    const reopened = startRequest();
    await finish(4, state.sessions[0], 'closed-result');
    await closed;
    assert(!list.innerHTML.includes('closed-result'));
    requests[5].resolve({ok:false, status:503});
    await reopened;
    assert(list.innerHTML.includes('Unable to search'));
    type('First');
    const wrongMode = startRequest();
    changeMode('name');
    await finish(6, state.sessions[1], 'wrong-mode');
    await wrongMode;
    assert(list.innerHTML.includes('First session') && !list.innerHTML.includes('wrong-mode'));
})().catch((error) => { console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", harness, str(_SESSION_CONTROLS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_completed_session_upgrade_remains_successful_if_refresh_fails() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Element {
    closest(selector) {
        return selector === '[data-session-action]' ? this : null;
    }
}
class HTMLButtonElement extends Element {}
class HTMLInputElement extends Element {}

let modal = null;
const list = { innerHTML: '' };
const count = { textContent: '' };
const alerts = [];
let loadCalled = false;
const log = { warn() {}, error() {} };

global.window = global;
global.window.confirm = () => true;
global.window.setTimeout = (callback) => { callback(); return 1; };
global.document = {
    body: {
        appendChild(node) { modal = node; },
    },
    createElement() {
        return {
            addEventListener(name, listener) { this.listeners[name] = listener; },
            listeners: {},
            querySelector(selector) {
                if (selector === '#session-browser-list') return list;
                if (selector === '#session-browser-count') return count;
                return null;
            },
            remove() { modal = null; },
        };
    },
    getElementById(id) {
        return id === 'session-browser-modal' ? modal : null;
    },
    querySelector() { return null; },
};
global.Element = Element;
global.HTMLButtonElement = HTMLButtonElement;
global.HTMLInputElement = HTMLInputElement;
global.alert = (message) => alerts.push(message);
global.fetch = async (url) => {
    assert.match(url, /upgrade-context-strategy/);
    return {
        ok: true,
        async json() {
            return { task: { task_id: 'task-1', status: 'completed' } };
        },
    };
};
global.console = log;

vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const session = { session_id: 'session-1', title: 'A session', can_upgrade_to_v2: true };
const controller = SessionControls.create({
    state: { sessions: [session], sessionId: 'session-1' },
    elements: { vaultSelector: { value: 'TestVault' } },
    icons: {},
    utils: { escapeHtml: (value) => String(value) },
    sessionSummary: { closePreview() {} },
    sessionMap: {},
    callbacks: {
        fetchSessions: async () => {},
        loadSession: async () => {
            loadCalled = true;
            throw new Error('reload unavailable');
        },
    },
});

(async () => {
    controller.openSessionBrowserModal();
    const button = new HTMLButtonElement();
    button.dataset = { sessionAction: 'upgrade-v2', sessionActionId: 'session-1' };
    button.disabled = false;
    await modal.listeners.click({
        target: button,
        preventDefault() {},
        stopPropagation() {},
    });

    assert.strictEqual(loadCalled, true, 'The active session should be refreshed after upgrade.');
    assert.strictEqual(alerts.length, 1, 'The operation should report one outcome.');
    assert.match(alerts[0], /^Session upgraded to Compaction v2,/);
    assert.match(alerts[0], /view could not refresh/);
    assert.match(alerts[0], /Reload the session/);
    assert.doesNotMatch(alerts[0], /Failed to upgrade/);
    assert.strictEqual(button.disabled, false, 'The upgrade action should be re-enabled.');
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_SESSION_CONTROLS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
