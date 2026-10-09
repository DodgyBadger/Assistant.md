"""Frontend smoke tests for chat task stream consumption."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _PROJECT_ROOT / "static/js/chat-task-stream.js"
_ACTIONS_MODULE_PATH = _PROJECT_ROOT / "static/js/chat-task-actions.js"


def test_chat_task_stream_consumes_sse_and_releases_matching_stream() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const activeController = new AbortController();
const state = {
    activeChatAbortController: activeController,
    activeChatSessionId: 'session-1',
    activeChatTaskId: 'task-1',
    isCancellingChat: false,
    isLoading: true,
};
const assistantMessage = {
    errorMessages: [],
    fullText: '',
};
const chatRendering = {
    appendAssistantDelta(message, delta) { message.fullText += delta; },
    appendAssistantThinkingDelta() {},
    handleToolEvent() {},
    renderAssistantMarkdown() {},
    resetAssistantStream() {},
    setAssistantStatus() {},
};
const controller = ChatTaskStream.create({
    state,
    chatRendering,
    callbacks: {
        handleDeferredReviewEvent() {},
        syncChatControlLocks() {},
    },
});

global.fetch = async () => new Response([
    'data: {"event":"delta","sequence":1,"choices":[{"delta":{"content":"Hello"}}]}',
    '',
    'data: {"event":"done","sequence":2,"choices":[{"finish_reason":"stop"}]}',
    '',
].join('\n'), {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
});

(async () => {
    const result = await controller.consumeEvents(
        'task-1',
        assistantMessage,
        activeController,
    );
    if (!result.finished || result.messageCount !== 1 || result.finishReason !== 'stop') {
        throw new Error(`Unexpected stream result: ${JSON.stringify(result)}`);
    }
    if (assistantMessage.fullText !== 'Hello') {
        throw new Error(`Unexpected assistant text: ${assistantMessage.fullText}`);
    }
    if (controller.releaseActiveStream(new AbortController())) {
        throw new Error('A stale stream controller released active state.');
    }
    if (!controller.releaseActiveStream(activeController)) {
        throw new Error('The active stream controller was not released.');
    }
    if (state.isLoading || state.activeChatTaskId !== null) {
        throw new Error('Active stream state was not cleared.');
    }
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE_PATH)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_chat_task_stream_loads_before_application_bootstrap() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    module_position = markup.index(
        '<script src="static/js/chat-task-stream.js"></script>'
    )
    actions_position = markup.index(
        '<script src="static/js/chat-task-actions.js"></script>'
    )
    application_position = markup.index('<script src="static/app.js"></script>')

    assert module_position < actions_position < application_position


def test_chat_task_actions_submit_and_finalize_text_request() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.alert = () => { throw new Error('Unexpected validation alert'); };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const state = {
    activeChatAbortController: null,
    activeChatSessionId: null,
    activeChatTaskId: null,
    isCancellingChat: false,
    isLoading: false,
    sessionId: null,
};
const elements = {
    chatInput: { value: 'Hello', focus() {} },
    chatModeSelector: { value: 'normal' },
    modelSelector: { value: 'test-model' },
    sendBtn: { disabled: false },
    templateSelector: { value: '' },
    thinkingSelector: { value: 'default' },
    vaultSelector: { value: 'TestVault' },
};
const assistantMessage = {
    artifactList: { childElementCount: 0 },
    fullText: 'Reply',
    messageDiv: { remove() {} },
    thinkingText: '',
    toolStatusMap: new Map(),
};
let finalized = false;
let loadedSession = false;
const chatRendering = {
    addErrorMessage(message) { throw new Error(message); },
    addLoadingMessage() { return {}; },
    addMessage() {},
    createAssistantStreamingMessage() { return assistantMessage; },
    finalizeAssistantMessage() { finalized = true; },
    getActiveToolDetailId() { return ''; },
    removeLoadingMessage() {},
    setAssistantStatus() {},
};
const chatTaskStream = {
    waitForTerminal(taskId, signal) {
        return new Promise((resolve, reject) => {
            pendingTerminals.push({ taskId, signal, resolve, reject });
            signal.addEventListener('abort', () => resolve(false), { once: true });
        });
    },
    async consumeEvents() {
        return { finishReason: 'stop', finished: true, messageCount: 1 };
    },
    releaseActiveStream(controller) {
        if (state.activeChatAbortController !== controller) return false;
        state.activeChatAbortController = null;
        state.activeChatSessionId = null;
        state.activeChatTaskId = null;
        state.isLoading = false;
        return true;
    },
};
const noop = () => {};
const pendingTerminals = [];
let pressureRefreshes = 0;
const deadlines = new Map();
let deadlineId = 0;
global.setTimeout = (callback) => { deadlines.set(++deadlineId, callback); return deadlineId; };
global.clearTimeout = (id) => deadlines.delete(id);
const flush = () => new Promise(resolve => setImmediate(resolve));
const controller = ChatTaskActions.create({
    state,
    composeState: { pendingAttachments: [] },
    elements,
    icons: { setIconButtonLabel: noop },
    chatRendering,
    chatTaskStream,
    sessionControls: {
        refreshCompactionProgress() { pressureRefreshes += 1; },
        renderSelector: noop,
        updateTitleRow: noop,
    },
    workspacePicker: { currentPath() { return ''; } },
    callbacks: {
        clearPendingAttachments: noop,
        createClientSessionId() { return 'client-session'; },
        async fetchSessions() {},
        async loadSession() { loadedSession = true; },
        async reconcileCommittedToolCalls() {},
        syncChatControlLocks: noop,
    },
});

global.fetch = async (url, options) => {
    if (url !== 'api/chat/tasks' || options.method !== 'POST') {
        throw new Error(`Unexpected request: ${url}`);
    }
    const payload = JSON.parse(options.body);
    if (payload.prompt !== 'Hello' || payload.session_id !== 'client-session') {
        throw new Error(`Unexpected request payload: ${options.body}`);
    }
    return new Response(JSON.stringify({
        session_id: 'server-session',
        task: { task_id: 'task-1' },
    }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
    });
};

(async () => {
    const sent = await controller.sendMessage();
    if (!sent || !finalized || !loadedSession) {
        throw new Error('Chat task request did not complete its UI lifecycle.');
    }
    if (state.isLoading || state.activeChatAbortController !== null) {
        throw new Error('Chat task request did not release active state.');
    }
    if (pendingTerminals.length !== 1) throw new Error('No post-turn completion observer.');
    const beforeCompletion = pressureRefreshes;
    pendingTerminals[0].resolve(true);
    await flush();
    if (pressureRefreshes !== beforeCompletion + 1) throw new Error('Pressure did not refresh after compaction.');
    if (deadlines.size) throw new Error('Completion observer deadline was not cleaned up.');

    async function sendAgain() {
        state.sessionId = null;
        elements.chatInput.value = 'Hello';
        if (!await controller.sendMessage()) throw new Error('Repeat turn did not finish immediately.');
        return pendingTerminals.at(-1);
    }
    const navigated = await sendAgain();
    state.sessionId = 'another-session';
    const beforeNavigation = pressureRefreshes;
    navigated.resolve(true);
    await flush();
    if (pressureRefreshes !== beforeNavigation) throw new Error('Old completion refreshed a different session.');

    const previous = await sendAgain();
    const newer = await sendAgain();
    if (!previous.signal.aborted) throw new Error('A newer turn did not cancel the old UI observer.');
    const beforeNewer = pressureRefreshes;
    previous.resolve(true);
    newer.resolve(true);
    await flush();
    if (pressureRefreshes !== beforeNewer + 1) throw new Error('Superseded completion refreshed pressure.');
    const timedOut = await sendAgain();
    deadlines.values().next().value();
    await flush();
    if (!timedOut.signal.aborted || deadlines.size) throw new Error('Completion observer was not bounded/cleaned up.');
    if (state.isLoading || state.activeChatAbortController !== null) throw new Error('Observer changed chat control state.');
    const unavailable = await sendAgain();
    const beforeUnavailable = pressureRefreshes;
    unavailable.reject(new Error('Task status unavailable'));
    await flush();
    if (pressureRefreshes !== beforeUnavailable || deadlines.size) throw new Error('Observer failure changed pressure or leaked its deadline.');
    if (state.isLoading || state.activeChatAbortController !== null) throw new Error('Observer failure locked chat.');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_ACTIONS_MODULE_PATH)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_terminal_waiter_handles_post_turn_delay_stale_context_and_failures() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
global.window = global;
global.setTimeout = callback => setImmediate(callback);
global.clearTimeout = clearImmediate;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const stream = ChatTaskStream.create({ state: {}, chatRendering: {}, callbacks: {} });
const signal = new AbortController().signal;
(async () => {
    let calls = 0;
    const statuses = ['queued', 'running', 'completed'];
    global.fetch = async (url, options) => {
        assert.strictEqual(url, 'api/tasks/task-1');
        assert.strictEqual(options.cache, 'no-store');
        assert.strictEqual(options.signal, signal);
        return { ok: true, json: async () => ({ status: statuses[calls++] }) };
    };
    assert.strictEqual(await stream.waitForTerminal('task-1', signal), true);
    assert.strictEqual(calls, 3);
    for (const status of ['failed', 'cancelled', 'timed_out', 'skipped']) {
        global.fetch = async () => ({ ok: true, json: async () => ({ status }) });
        assert.strictEqual(await stream.waitForTerminal('task-1', signal), true);
    }
    let current = true;
    global.fetch = async () => ({ ok: true, json: async () => {
        current = false;
        return { status: 'completed' };
    } });
    assert.strictEqual(await stream.waitForTerminal('task-1', signal, { isCurrent: () => current }), false);
    global.fetch = async () => { throw new Error('Stale observer must not fetch'); };
    assert.strictEqual(await stream.waitForTerminal('task-1', signal, { isCurrent: () => false }), false);
    for (const response of [{ ok: false, status: 404 }, { ok: true, json: async () => ({}) }]) {
        calls = 0;
        global.fetch = async () => { calls++; return response; };
        await assert.rejects(stream.waitForTerminal('task-1', signal));
        assert.strictEqual(calls, 3, 'Failure retries must be bounded, including malformed task status.');
    }
    const cancelled = new AbortController();
    global.fetch = async (_url, { signal }) => new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')));
    });
    const pending = stream.waitForTerminal('task-1', cancelled.signal);
    cancelled.abort();
    await assert.rejects(pending, { name: 'AbortError' });
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE_PATH)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
