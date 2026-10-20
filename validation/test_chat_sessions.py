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
        async refreshCompactionState() {},
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

    elements.chatModeSelector = {value:'inline_edit'};
    const staleModeLoad = controller.loadSession('new', {skipActiveTaskCheck:true});
    state.chatModeRevision = {vault:'vault-b',sessionId:'new'};
    elements.chatModeSelector.value = 'normal';
    succeed(requests.at(-1), {session_id:'new',chat_mode:'inline_edit'});
    await staleModeLoad;
    assert.strictEqual(elements.chatModeSelector.value, 'normal', 'A history response predating a preference change must not reset it.');

    state.chatModeSavePending = {vault:'vault-b',sessionId:'new'};
    const pendingModeLoad = controller.loadSession('new', {skipActiveTaskCheck:true});
    succeed(requests.at(-1), {session_id:'new',chat_mode:'inline_edit'});
    await pendingModeLoad;
    assert.strictEqual(elements.chatModeSelector.value, 'normal', 'History reload must preserve a preference while its save is pending.');

    state.chatModeSavePending = null;
    const savedModeLoad = controller.loadSession('new', {skipActiveTaskCheck:true});
    succeed(requests.at(-1), {session_id:'new',chat_mode:'inline_edit'});
    await savedModeLoad;
    assert.strictEqual(elements.chatModeSelector.value, 'inline_edit', 'Without a racing save, history loads restore the persisted mode.');

    const otherSessionSaveLoad = controller.loadSession('new', {skipActiveTaskCheck:true});
    state.chatModeRevision = {vault:'vault-b',sessionId:'other'};
    succeed(requests.at(-1), {session_id:'new',chat_mode:'normal'});
    await otherSessionSaveLoad;
    assert.strictEqual(elements.chatModeSelector.value, 'normal', 'A save finishing for another session must not suppress this session’s persisted preference.');


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


def test_pending_chat_mode_save_survives_session_navigation() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
global.window = global;
global.document = {};
for (const module of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(module, 'utf8'), {filename:module});
}

async function exercise({ok, returnToA, historyAfterSave}) {
    const state = {
        sessionId:'A', isLoading:false,
        sessions:[{session_id:'A',chat_mode:'normal'}, {session_id:'B',chat_mode:'normal'}],
    };
    const elements = {vaultSelector:{value:'vault'}, chatModeSelector:{value:'normal'}};
    let finishSave;
    let finishHistory;
    const errors = [];
    global.fetch = async (url, options) => {
        if (options?.method === 'PATCH') {
            return new Promise(resolve => { finishSave = resolve; });
        }
        const sessionId = url.includes('/B?') ? 'B' : 'A';
        const response = {ok:true, json:async () => ({session_id:sessionId, chat_mode:'normal'})};
        if (sessionId === 'A' && historyAfterSave) {
            return new Promise(resolve => { finishHistory = () => resolve(response); });
        }
        return response;
    };
    const sessionControls = {renderSelector() {}, async refreshCompactionState() {}};
    const callbacks = {syncControls() {}, updateStatus() {}, addErrorMessage(message) {errors.push(message);}};
    const selection = ChatSelection.create({state, elements, sessionControls, callbacks});
    const sessions = ChatSessions.create({
        state, elements, sessionControls, callbacks,
        chatRendering:{closeToolCallDetails() {}, renderPersistedSession() {}},
        chatTaskActions:{}, chatTaskStream:{},
    });
    const save = selection.toggleFileApprovals();
    assert.strictEqual(elements.chatModeSelector.value, 'inline_edit');
    await sessions.loadSession('B', {skipActiveTaskCheck:true});
    assert.strictEqual(elements.chatModeSelector.value, 'normal', 'A pending save must not replace another session’s mode.');
    let returnLoad;
    if (returnToA) {
        returnLoad = sessions.loadSession('A', {skipActiveTaskCheck:true});
        if (!historyAfterSave) {
            await returnLoad;
            assert.strictEqual(elements.chatModeSelector.value, 'inline_edit', 'Returning to A must restore its pending mode, not retain B’s mode.');
        }
    }
    finishSave({ok, status:ok ? 200 : 500});
    await save;
    if (finishHistory) {
        finishHistory();
        await returnLoad;
    }
    assert.strictEqual(state.sessions[0].chat_mode, ok ? 'inline_edit' : 'normal');
    assert.strictEqual(elements.chatModeSelector.value, returnToA && ok ? 'inline_edit' : 'normal');
    assert.strictEqual(errors.length, ok ? 0 : 1);
    assert.strictEqual(state.chatModeSavePending, null);
}
(async () => {
    const originalError = console.error;
    console.error = () => {};
    try {
        for (const ok of [true, false]) {
            for (const returnToA of [true, false]) {
                for (const historyAfterSave of [true, false]) {
                    await exercise({ok, returnToA, historyAfterSave});
                }
            }
        }
    } finally { console.error = originalError; }
})().catch(error => {process.stderr.write(String(error.stack || error)); process.exit(1);});
"""
    subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(_MODULE),
            str(_PROJECT_ROOT / "static/js/chat-selection.js"),
        ],
        check=True,
        cwd=_PROJECT_ROOT,
    )
