"""Frontend smoke tests for session controls."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SESSION_CONTROLS_MODULE = _PROJECT_ROOT / "static/js/session-controls.js"


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
