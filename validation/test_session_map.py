"""Frontend smoke tests for read-only stepped session-map inspection."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/session-map.js"


def test_session_map_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = { getElementById: () => null };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = SessionMap.create({
    elements: {},
    icons: {},
    utils: { escapeHtml: value => String(value) },
});

for (const name of ['closeModal', 'openModalForSession']) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing session-map method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_session_map_escape_focus_and_restore_collapsed_map_checkpoint() -> None:
    tool_harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Element {
    constructor() { this.listeners = {}; this.isConnected = true; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    removeEventListener(name) { delete this.listeners[name]; }
    focus() { document.activeElement = this; }
    closest() { return null; }
    remove() { this.isConnected = false; if (activeModal === this) activeModal = null; }
    querySelector(selector) {
        if (selector === '[data-session-map-dialog]') return this.dialog;
        if (selector === '#session-map-modal-body') return this.body;
        if (selector === '.session-map-content') return this.mapDetails;
        return null;
    }
}
class HTMLButtonElement extends Element {}
class HTMLSelectElement extends Element {}

let activeModal = null;
const requests = [];
const trigger = new Element();
global.window = global;
global.document = {
    activeElement: null,
    body: { appendChild(modal) { activeModal = modal; } },
    createElement() {
        const modal = new Element();
        modal.dialog = new Element();
        modal.body = { innerHTML: '' };
        modal.mapDetails = { open: false };
        modal.querySelector = Element.prototype.querySelector;
        return modal;
    },
    getElementById(id) { return id === 'session-map-modal' ? activeModal : null; },
};
document.activeElement = trigger;
global.Element = Element;
global.HTMLButtonElement = HTMLButtonElement;
global.HTMLSelectElement = HTMLSelectElement;
global.fetch = async (url) => {
    requests.push(url);
    return {
        ok: true,
        async json() {
            return {
                session_map: { entries: [] },
                revisions: [{ checkpoint_id: 'cp-1', revision: 1 }],
                selected_checkpoint_id: 'cp-1',
            };
        },
    };
};
global.console = { error() {} };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = SessionMap.create({
    elements: { vaultSelector: { value: 'TestVault' } },
    icons: { MAP_ICON_SVG: '', FORK_ICON_SVG: '', X_ICON_SVG: '', ARROW_LEFT_ICON_SVG: '' },
    utils: { escapeHtml: value => String(value) },
});

(async () => {
    await controller.openModalForSession({ session_id: 'session-1' });
    assert.strictEqual(document.activeElement, activeModal.dialog, 'Opening the map should focus its dialog.');
    activeModal.listeners.keydown({
        key: 'Escape', preventDefault() {}, stopPropagation() {},
    });
    assert.strictEqual(activeModal, null, 'Escape should close the session-map modal.');
    assert.strictEqual(document.activeElement, trigger, 'Closing should restore focus to the invoker.');

    await controller.openModalForSession({ session_id: 'session-1' }, {
        checkpointId: 'cp-1', mapOpen: false,
    });
    assert.match(requests.at(-1), /checkpoint_id=cp-1/);
    assert.doesNotMatch(requests.at(-1), /message_page/);
    assert.match(activeModal.body.innerHTML, /<details class="session-map-content">/);
    assert.doesNotMatch(activeModal.body.innerHTML, /<details class="session-map-content" open>/);
    activeModal.listeners.keydown({
        key: 'Escape', preventDefault() {}, stopPropagation() {},
    });
    assert.strictEqual(activeModal, null, 'Escape should close the restored map.');
    assert.strictEqual(document.activeElement, trigger, 'The restored map should retain its original invoker.');
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    subprocess.run(
        ["node", "-e", tool_harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_session_map_ignores_out_of_order_checkpoint_results() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Element {
    constructor() { this.listeners = {}; this.isConnected = true; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    removeEventListener(name) { delete this.listeners[name]; }
    focus() {}
    remove() { this.isConnected = false; }
    querySelector(selector) {
        if (selector === '#session-map-modal-body') return this.body;
        if (selector === '[data-session-map-dialog]') return this;
        if (selector === '.session-map-content') return { open: true };
        return null;
    }
}
class HTMLButtonElement extends Element {}
class HTMLSelectElement extends Element {
    matches() { return true; }
}
global.window = global;
global.Element = Element;
global.HTMLButtonElement = HTMLButtonElement;
global.HTMLSelectElement = HTMLSelectElement;
let modal;
global.document = {
    activeElement: new Element(),
    body: { appendChild(value) { modal = value; } },
    createElement() { const value = new Element(); value.body = { innerHTML: '' }; return value; },
    getElementById() { return modal; },
};
const requests = [];
global.fetch = () => new Promise((resolve, reject) => requests.push({ resolve, reject }));
const errors = [];
global.console = { error(value) { errors.push(value); } };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });
const controller = SessionMap.create({
    elements: { vaultSelector: { value: 'vault' } },
    icons: {},
    utils: { escapeHtml: value => String(value) },
});
const payload = marker => ({
    session_map: { entries: [{ kind: 'note', text: marker }] },
    revisions: [{ checkpoint_id: 'cp-1', revision: 1 }],
    selected_checkpoint_id: 'cp-1',
});
const succeed = (request, marker) => request.resolve({ ok: true, json: async () => payload(marker) });

(async () => {
    const opening = controller.openModalForSession({ session_id: 'session' });
    succeed(requests[0], 'initial');
    await opening;

    const staleRevision = new HTMLSelectElement();
    staleRevision.value = 'cp-stale';
    modal.listeners.change({ target: staleRevision });
    const revision = new HTMLSelectElement();
    revision.value = 'cp-1';
    modal.listeners.change({ target: revision });
    succeed(requests[2], 'latest revision');
    await Promise.resolve();
    await Promise.resolve();
    succeed(requests[1], 'stale revision');
    await Promise.resolve();
    await Promise.resolve();
    assert.match(modal.body.innerHTML, /latest revision/);
    assert.doesNotMatch(modal.body.innerHTML, /stale revision/);

    staleRevision.value = 'cp-old-error';
    modal.listeners.change({ target: staleRevision });
    modal.listeners.change({ target: revision });
    succeed(requests[4], 'latest after error');
    await Promise.resolve();
    await Promise.resolve();
    requests[3].reject(new Error('old request failed'));
    await Promise.resolve();
    await Promise.resolve();
    assert.match(modal.body.innerHTML, /latest after error/);
    assert.deepStrictEqual(errors, []);
})().catch(error => { process.stderr.write(String(error.stack || error)); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_session_map_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    map_position = markup.index('<script src="static/js/session-map.js"></script>')
    application_position = markup.index('<script src="static/app.js"></script>')

    assert map_position < application_position


def test_session_map_renders_narrative_trajectory() -> None:
    source = _MODULE.read_text(encoding="utf-8")

    assert "How We Got Here" in source
    assert "renderSources(trajectory)" in source
    assert '<details class="session-map-content"' in source
    assert "options.mapOpen === false" in source
    assert "formatDate(selected?.created_at)" in source
    assert "selected?.prompt_contract_version" not in source
    assert "humanize(selected?.action)" not in source


def test_session_map_keeps_transcript_navigation_in_chat_timeline() -> None:
    map_source = _MODULE.read_text(encoding="utf-8")
    history_source = (_PROJECT_ROOT / "static/js/chat-history-rendering.js").read_text(
        encoding="utf-8"
    )

    assert "renderTranscript" not in map_source
    assert "data-session-map-transcript-page" not in map_source
    assert "payload.transcript" not in map_source
    assert "session-map-checkpoint-link" in history_source
    assert "payload?.context_checkpoint_kind === 'session_map'" in history_source
    assert "Load older messages" in history_source
    assert "/timeline" in history_source
    assert "callbacks.openSessionMap" in history_source
