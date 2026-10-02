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


def test_session_map_escape_focus_and_tool_detail_back_restore_checkpoint() -> None:
    tool_harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Element {
    constructor() { this.listeners = {}; this.isConnected = true; }
    addEventListener(name, handler) { this.listeners[name] = handler; }
    removeEventListener(name) { delete this.listeners[name]; }
    focus() { document.activeElement = this; }
    closest(selector) {
        return selector === '[data-session-map-tool-call]' ? this : null;
    }
    getAttribute(name) { return this.attributes?.[name] ?? null; }
    remove() { this.isConnected = false; if (activeModal === this) activeModal = null; }
    querySelector(selector) {
        if (selector === '[data-session-map-dialog]') return this.dialog;
        if (selector === '#session-map-modal-body') return this.body;
        if (selector === '.session-map-content') return this.mapDetails;
        if (selector === '.session-map-transcript') return this.transcriptDetails;
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
        modal.transcriptDetails = { open: false };
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
                transcript: {
                    checkpoint_id: 'cp-1', page: 2, page_count: 3, total_entries: 5,
                    has_previous: true, has_next: true,
                    messages: [{
                        is_tool_message: true,
                        tool_calls: [{ tool_call_id: 'tool-1', tool_name: 'lookup' }],
                    }],
                },
            };
        },
    };
};
global.console = { error() {} };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

let toolOptions = null;
const controller = SessionMap.create({
    elements: { vaultSelector: { value: 'TestVault' } },
    icons: { MAP_ICON_SVG: '', FORK_ICON_SVG: '', X_ICON_SVG: '', ARROW_LEFT_ICON_SVG: '' },
    utils: { escapeHtml: value => String(value) },
    callbacks: { openToolCall(options) { toolOptions = options; } },
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
        checkpointId: 'cp-1', messagePage: 2, transcriptOpen: true, mapOpen: false,
    });
    const toolButton = new HTMLButtonElement();
    toolButton.attributes = {
        'data-session-map-tool-call': 'tool-1',
        'data-session-map-tool-name': 'lookup',
        'data-session-map-tool-state': 'completed',
        'data-session-map-tool-tokens': '3',
        'data-session-map-tool-checkpoint': 'cp-1',
        'data-session-map-tool-page': '2',
    };
    await activeModal.listeners.click({ target: toolButton });
    assert(toolOptions, 'Selecting transcript tool detail should open the nested detail.');
    assert.strictEqual(activeModal, null, 'Opening nested detail should replace the map modal.');
    assert.strictEqual(toolOptions.returnFocusTarget, trigger, 'Closing nested detail should restore the original map invoker.');
    await toolOptions.onBack();
    assert.strictEqual(document.activeElement, activeModal.dialog, 'Returning should focus the restored map dialog.');
    assert.match(requests.at(-1), /checkpoint_id=cp-1&message_page=2/);
    assert.match(activeModal.body.innerHTML, /<details class="session-map-transcript" open>/);
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


def test_session_map_exposes_checkpoint_and_transcript_actions() -> None:
    map_source = _MODULE.read_text(encoding="utf-8")
    history_source = (_PROJECT_ROOT / "static/js/chat-history-rendering.js").read_text(
        encoding="utf-8"
    )

    assert "renderTranscript(payload.transcript" in map_source
    assert "data-session-map-transcript-page" in map_source
    assert (
        "loadCheckpoint(modal, session.session_id, checkpointId, page, true, mapOpen)"
        in map_source
    )
    assert "data-session-map-tool-call" in map_source
    assert "callbacks.openToolCall" in map_source
    assert "data-session-map-fork" in map_source
    assert "callbacks.forkSession" in map_source
    assert "message?.role === 'assistant'" in map_source
    assert (
        '<details class="session-map-transcript-message session-map-transcript-tools">'
        in map_source
    )
    assert "session-map-checkpoint-link" in history_source
    assert "message.context_checkpoint_kind === 'session_map'" in history_source
    assert "callbacks.openSessionMap" in history_source
