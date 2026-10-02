"""Frontend smoke tests for chat session coordination."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/chat-sessions.js"


def test_chat_sessions_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = ChatSessions.create({
    state: {},
    elements: {},
    sessionControls: {},
    chatRendering: {},
    chatTaskActions: {},
    chatTaskStream: {},
    callbacks: {},
});

for (const name of ['fetchSessions', 'loadSession', 'reconcileCommittedToolCalls', 'reattachActiveTask']) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing chat sessions method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_chat_sessions_ignores_stale_vault_and_session_responses() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
const requests = [];
global.fetch = url => new Promise((resolve, reject) => requests.push({ url, resolve, reject }));
const logs = [];
global.console = { error(message) { logs.push(message); } };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const state = { sessions: [], sessionId: null, isLoading: false };
const elements = { vaultSelector: { value: 'vault-a' } };
const rendered = [];
const errors = [];
const controller = ChatSessions.create({
    state,
    elements,
    sessionControls: {
        renderSelector() {},
        clearCompactionProgress() {},
        async refreshCompactionProgress() {},
    },
    chatRendering: {
        closeToolCallDetails() {},
        renderPersistedSession(payload) { rendered.push(payload.session_id); },
    },
    chatTaskActions: {},
    chatTaskStream: {},
    callbacks: {
        syncControls() {},
        updateStatus() {},
        addErrorMessage(message) { errors.push(message); },
    },
});
const succeed = (request, payload) => request.resolve({ ok: true, json: async () => payload });

(async () => {
    const oldList = controller.fetchSessions('vault-a', 'old');
    elements.vaultSelector.value = 'vault-b';
    const newList = controller.fetchSessions('vault-b', 'new');
    succeed(requests[1], [{ session_id: 'new' }]);
    await newList;
    succeed(requests[0], [{ session_id: 'old' }]);
    await oldList;
    assert.deepStrictEqual(state.sessions, [{ session_id: 'new' }]);
    assert.strictEqual(state.sessionId, 'new');

    const oldLoad = controller.loadSession('old', { skipActiveTaskCheck: true });
    const newLoad = controller.loadSession('new', { skipActiveTaskCheck: true });
    assert.strictEqual(state.isLoading, true);
    succeed(requests[2], { session_id: 'old' });
    await oldLoad;
    assert.strictEqual(state.isLoading, true, 'An older load must not clear a newer load’s busy state.');
    assert.deepStrictEqual(rendered, []);
    requests[3].reject(new Error('current load failed'));
    await newLoad;
    assert.deepStrictEqual(errors, ['current load failed']);
    assert.strictEqual(state.isLoading, false);

    const firstSameSession = controller.loadSession('new', { skipActiveTaskCheck: true });
    const secondSameSession = controller.loadSession('new', { skipActiveTaskCheck: true });
    requests[4].reject(new Error('stale load failed'));
    await firstSameSession;
    assert.strictEqual(state.isLoading, true);
    assert.deepStrictEqual(errors, ['current load failed']);
    succeed(requests[5], { session_id: 'new', history_revision: 1 });
    await secondSameSession;
    assert.deepStrictEqual(rendered, ['new']);
    assert.strictEqual(state.isLoading, false);

    const oldErrorList = controller.fetchSessions('vault-b');
    const currentList = controller.fetchSessions('vault-b');
    succeed(requests[7], [{ session_id: 'new' }]);
    await currentList;
    requests[6].reject(new Error('stale list failed'));
    await oldErrorList;
    assert.deepStrictEqual(logs, ['Error loading chat session:']);

    const preferredList = controller.fetchSessions('vault-b', 'new');
    const preferredRequest = requests.at(-1);
    state.sessionId = 'chosen';
    succeed(preferredRequest, [{ session_id: 'new' }, { session_id: 'chosen' }]);
    await preferredList;
    assert.strictEqual(state.sessionId, 'chosen', 'A refresh must preserve a newer session selection.');

    const firstTaskLoad = controller.loadSession('new');
    const firstTaskLoadRequest = requests.at(-1);
    succeed(firstTaskLoadRequest, { session_id: 'new', history_revision: 1 });
    await Promise.resolve();
    await Promise.resolve();
    const oldTaskRequest = requests.at(-1);
    assert.notStrictEqual(oldTaskRequest, firstTaskLoadRequest);
    const replacementLoad = controller.loadSession('new', { skipActiveTaskCheck: true });
    succeed(requests.at(-1), { session_id: 'new', history_revision: 2 });
    await replacementLoad;
    succeed(oldTaskRequest, { task_id: 'old-task', status: 'running' });
    await firstTaskLoad;
    assert.strictEqual(state.activeChatTaskId, undefined);
    assert.strictEqual(state.isLoading, false);
})().catch(error => { process.stderr.write(String(error.stack || error)); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_chat_sessions_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    sessions_position = markup.index(
        '<script src="static/js/chat-sessions.js"></script>'
    )
    application_position = markup.index('<script src="static/app.js"></script>')

    assert sessions_position < application_position
