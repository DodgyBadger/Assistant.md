"""Frontend focus-lifecycle tests for nested tool details."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/chat-tool-details.js"


def test_tool_detail_back_preserves_map_focus_and_escape_restores_connected_invoker() -> (
    None
):
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Element {
    constructor() { this.listeners = {}; this.isConnected = true; this.dataset = {}; }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    appendChild() {}
    focus() { document.activeElement = this; }
    setAttribute() {}
    closest(selector) { return selector === '[data-tool-call-back="true"]' ? this : null; }
    remove() { this.isConnected = false; if (activeModal === this) activeModal = null; }
    replaceChildren() {}
    querySelector(selector) { return selector === '[data-tool-call-dialog]' ? this.dialog : null; }
}

let activeModal = null;
let documentKeydown = null;
const body = new Element();
const mapDialog = new Element();
const connectedInvoker = new Element();
const backButton = new Element();
global.window = global;
global.document = {
    activeElement: connectedInvoker,
    addEventListener(name, listener) { if (name === 'keydown') documentKeydown = listener; },
    removeEventListener(name) { if (name === 'keydown') documentKeydown = null; },
    body: { appendChild(modal) { activeModal = modal; } },
    createElement() {
        const modal = new Element();
        modal.dialog = new Element();
        return modal;
    },
    getElementById(id) { return id === 'chat-tool-call-modal' ? activeModal : null; },
    querySelector(selector) {
        return selector === '#chat-tool-call-modal [data-tool-call-modal-body]' ? body : null;
    },
};
global.Element = Element;
global.fetch = async () => { throw new Error('No load should be requested for this smoke test.'); };
global.AbortController = class { constructor() { this.signal = {}; } abort() {} };
global.console = { error() {} };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = ChatToolDetails.create({
    state: {},
    elements: {},
    icons: { X_ICON_SVG: '', ARROW_LEFT_ICON_SVG: '' },
    utils: { escapeHtml: value => String(value) },
    callbacks: {
        toolStateLabel: entry => entry.state,
        formatToolElapsed: () => '0 seconds',
        createCopyButton: () => new Element(),
    },
});

let backCalled = false;
controller.openPersisted({
    toolId: 'tool-1', toolName: 'lookup', state: 'completed', persisted: true,
    returnFocusTarget: connectedInvoker,
    onBack() { backCalled = true; mapDialog.focus(); },
});
assert.strictEqual(document.activeElement, activeModal.dialog, 'Opening the detail should focus its dialog.');
activeModal.listeners.click({ target: backButton });
assert.strictEqual(backCalled, true, 'The nested Back action should return to the parent map.');
assert.strictEqual(document.activeElement, mapDialog, 'Back should not steal focus from the reopened map.');
assert.strictEqual(activeModal, null, 'The nested tool detail should close on Back.');

connectedInvoker.isConnected = true;
controller.openPersisted({
    toolId: 'tool-2', toolName: 'search', state: 'completed', persisted: true,
    returnFocusTarget: connectedInvoker,
});
assert.strictEqual(document.activeElement, activeModal.dialog);
documentKeydown({ key: 'Escape', preventDefault() {}, stopPropagation() {} });
assert.strictEqual(activeModal, null, 'Escape should close the top tool-detail modal.');
assert.strictEqual(document.activeElement, connectedInvoker, 'Escape should restore focus to a connected invoker.');
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
