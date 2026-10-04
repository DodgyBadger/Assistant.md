"""Frontend smoke tests for the chat rendering module graph."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DETAILS_MODULE = _PROJECT_ROOT / "static/js/chat-tool-details.js"
_MARKDOWN_MODULE = _PROJECT_ROOT / "static/js/chat-markdown.js"
_MESSAGE_CONTROLS_MODULE = _PROJECT_ROOT / "static/js/chat-message-controls.js"
_HISTORY_MODULE = _PROJECT_ROOT / "static/js/chat-history-rendering.js"
_START_PANEL_MODULE = _PROJECT_ROOT / "static/js/chat-start-panel.js"
_THINKING_MODULE = _PROJECT_ROOT / "static/js/chat-thinking.js"
_RENDERING_MODULE = _PROJECT_ROOT / "static/js/chat-rendering.js"
_TASK_STREAM_MODULE = _PROJECT_ROOT / "static/js/chat-task-stream.js"


def test_chat_rendering_composes_tool_details_controller() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

const noop = () => {};
const controller = ChatRendering.create({
    state: {},
    elements: {},
    icons: {},
    utils: {},
    callbacks: {
        fetchSessions: noop,
        loadSession: noop,
        renderEditProposalArtifact: noop,
        scrollChatToBottom: noop,
    },
});

for (const name of [
    'closeToolCallDetails',
    'forkSession',
    'getActiveToolDetailId',
    'handleToolEvent',
    'reconcileToolCallPersistence',
    'renderAssistantMarkdown',
]) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing chat rendering method: ${name}`);
    }
}
if (controller.getActiveToolDetailId() !== '') {
    throw new Error('Tool details should initialize without an active entry.');
}
"""
    subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(_DETAILS_MODULE),
            str(_MARKDOWN_MODULE),
            str(_MESSAGE_CONTROLS_MODULE),
            str(_HISTORY_MODULE),
            str(_START_PANEL_MODULE),
            str(_THINKING_MODULE),
            str(_RENDERING_MODULE),
        ],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_assistant_markdown_scroll_option_is_defined_and_respected() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
global.ChatMarkdown = { create: () => ({
    renderHtml() {}, flushPostProcess() {}, schedulePostProcess() {}, renderPreview() {},
}) };
global.ChatMessageControls = { create: () => ({
    addMessage() {}, addLoadingMessage() {}, removeLoadingMessage() {},
    createCopyButton() {}, createForkButton() {}, forkSession() {},
}) };
global.ChatToolDetails = { create: () => ({
    close() {}, getActiveId() { return ''; }, openPersisted() {},
}) };
global.ChatStartPanel = { create: () => ({ render() {}, refresh() {} }) };
global.ChatThinking = { create: () => ({ render() {} }) };
global.ChatHistoryRendering = { create: () => ({ renderSession() {}, clear() {} }) };

vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });
let scrollCount = 0;
const controller = ChatRendering.create({
    state: {}, elements: {}, icons: {}, utils: {},
    callbacks: {
        scrollChatToBottom() { scrollCount += 1; },
    },
});
const context = { bodyDiv: {}, fullText: '', thinkingText: '' };
controller.renderAssistantMarkdown(context);
assert.strictEqual(scrollCount, 1, 'Normal rendering should follow the live response.');
controller.renderAssistantMarkdown(context, { forceScroll: false });
assert.strictEqual(scrollCount, 1, 'Historical prepending must not force a scroll.');
"""
    subprocess.run(
        ["node", "-e", harness, str(_RENDERING_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_tool_details_load_before_chat_rendering() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    details_position = markup.index(
        '<script src="static/js/chat-tool-details.js"></script>'
    )
    markdown_position = markup.index(
        '<script src="static/js/chat-markdown.js"></script>'
    )
    message_controls_position = markup.index(
        '<script src="static/js/chat-message-controls.js"></script>'
    )
    history_position = markup.index(
        '<script src="static/js/chat-history-rendering.js"></script>'
    )
    start_panel_position = markup.index(
        '<script src="static/js/chat-start-panel.js"></script>'
    )
    thinking_position = markup.index(
        '<script src="static/js/chat-thinking.js"></script>'
    )
    rendering_position = markup.index(
        '<script src="static/js/chat-rendering.js"></script>'
    )

    assert (
        details_position
        < markdown_position
        < message_controls_position
        < history_position
        < start_panel_position
        < thinking_position
        < rendering_position
    )


def test_fork_confirmation_explains_compaction_inheritance() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

let confirmation = '';
let fetchCalled = false;
global.window = global;
global.window.confirm = (message) => {
    confirmation = message;
    return false;
};
global.fetch = async () => {
    fetchCalled = true;
    throw new Error('Fetch should not run after cancelling confirmation.');
};
global.document = {
    createElement: () => ({
        disabled: false,
        listeners: {},
        setAttribute() {},
        addEventListener(name, listener) {
            this.listeners[name] = listener;
        },
    }),
};

vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const controller = ChatMessageControls.create({
    state: { sessionId: 'source-session', isLoading: false },
    elements: { vaultSelector: { value: 'TestVault' } },
    icons: { FORK_ICON_SVG: '<svg></svg>' },
    utils: {},
    markdown: {},
    callbacks: {},
});
const button = controller.createForkButton(7);
if (!button) throw new Error('Expected a fork button.');

(async () => {
    await button.listeners.click({ stopPropagation() {} });
    if (!confirmation.includes('compaction state')) {
        throw new Error('Confirmation should explain inherited compaction state.');
    }
    if (!confirmation.includes('upgrade the fork to Compaction V2 again')) {
        throw new Error('Confirmation should explain that V2 may need another upgrade.');
    }
    if (fetchCalled) {
        throw new Error('Cancelling confirmation should not call the fork API.');
    }
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_MESSAGE_CONTROLS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_persisted_user_message_respects_scroll_suppression() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {
    createElement() {
        return {
            children: [], className: '', innerHTML: '', title: '', type: '',
            classList: { add() {} },
            appendChild(child) { this.children.push(child); return child; },
            addEventListener() {}, setAttribute() {},
        };
    },
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const appendOptions = [];
const controller = ChatMessageControls.create({
    state: {}, elements: {}, icons: { COPY_ICON_SVG: '' },
    utils: { getCopyableText() { return ''; } },
    markdown: {},
    callbacks: {
        appendMessageNode(_node, options) { appendOptions.push(options); },
    },
});
controller.addMessage('user', 'An older persisted message.', {
    sequenceIndex: 3,
    forceScroll: false,
});
assert.deepStrictEqual(
    appendOptions,
    [{ forceScroll: false }],
    'Prepending a persisted user message must not scroll the chat to the bottom.'
);
"""
    subprocess.run(
        ["node", "-e", harness, str(_MESSAGE_CONTROLS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_persisted_assistant_fork_requires_explicit_canonical_origin() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const assistantForkIndexes = [];
const userDisplayIndexes = [];
const controller = ChatHistoryRendering.create({
    state: { sessionId: 'session' },
    elements: { chatMessages: { innerHTML: '' } },
    icons: {},
    toolDetails: { close() {} },
    messageControls: {
        addMessage(_role, _content, options) {
            userDisplayIndexes.push(options.sequenceIndex);
        },
    },
    callbacks: {
        createAssistantStreamingMessage() { return {}; },
        renderAssistantMarkdown() {},
        finalizeAssistantMessage(context) {
            assistantForkIndexes.push(context.sequenceIndex);
        },
    },
});

controller.renderSession({
    messages: [
        {
            role: 'assistant',
            sequence_index: 0,
            fork_sequence_index: null,
            content: 'Legacy replacement with no trustworthy canonical origin.',
        },
        {
            role: 'assistant',
            sequence_index: 1,
            fork_sequence_index: 42,
            content: 'Canonical assistant message.',
        },
        {
            role: 'user',
            sequence_index: 2,
            fork_sequence_index: null,
            content: 'A user message keeps its display position.',
        },
    ],
});

assert.deepStrictEqual(assistantForkIndexes, [null, 42]);
assert.deepStrictEqual(userDisplayIndexes, [2]);
"""
    subprocess.run(
        ["node", "-e", harness, str(_HISTORY_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_persisted_tool_turn_renders_as_one_assistant_message() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const rendered = [];
const controller = ChatHistoryRendering.create({
    state: { sessionId: 'session' },
    elements: { chatMessages: { innerHTML: '' } },
    icons: {},
    toolDetails: { close() {}, normalizeTokenCount() {}, setEntryTokenCount() {} },
    messageControls: { addMessage() {} },
    callbacks: {
        createAssistantStreamingMessage() {
            return { toolStatusMap: new Map() };
        },
        renderAssistantMarkdown() {},
        ensureToolCallsSection() {},
        createToolStatusEntry(context, toolId, payload) {
            const entry = { toolId, toolName: payload.tool_name, state: 'running' };
            context.toolStatusMap.set(toolId, entry);
            return entry;
        },
        setToolEntryState(entry, state) { entry.state = state; },
        updateToolCallsSummary() {},
        finalizeAssistantMessage(context) {
            rendered.push({
                content: context.fullText,
                thinking: context.thinkingText,
                sequenceIndex: context.sequenceIndex,
                toolIds: Array.from(context.toolStatusMap.keys()),
            });
        },
    },
});

controller.renderSession({
    messages: [
        { role: 'user', sequence_index: 0, content: 'Research this.' },
        {
            role: 'assistant', sequence_index: 1, fork_sequence_index: null,
            is_tool_message: true, content: '', thinking_content: 'First check.',
            tool_call_ids: ['call-1'], tool_return_ids: [],
        },
        {
            role: 'user', sequence_index: 2, is_tool_message: true, content: '',
            tool_call_ids: [], tool_return_ids: ['call-1'],
        },
        {
            role: 'assistant', sequence_index: 3, fork_sequence_index: null,
            is_tool_message: true, content: 'I found a lead.', thinking_content: '',
            tool_call_ids: ['call-2'], tool_return_ids: [],
        },
        {
            role: 'user', sequence_index: 4, is_tool_message: true, content: '',
            tool_call_ids: [], tool_return_ids: ['call-2'],
        },
        {
            role: 'assistant', sequence_index: 5, fork_sequence_index: 5,
            is_tool_message: false, content: 'Final answer.', thinking_content: '',
            tool_call_ids: [], tool_return_ids: [],
        },
    ],
    tool_calls: [
        { tool_call_id: 'call-1', tool_name: 'search', status: 'completed' },
        { tool_call_id: 'call-2', tool_name: 'read', status: 'completed' },
    ],
});

assert.deepStrictEqual(rendered, [{
    content: 'I found a lead.\n\nFinal answer.',
    thinking: 'First check.',
    sequenceIndex: 5,
    toolIds: ['call-1', 'call-2'],
}]);

rendered.length = 0;
controller.renderSession({
    messages: [
        { role: 'user', sequence_index: 0, content: 'Start a review.' },
        {
            role: 'assistant', sequence_index: 1, fork_sequence_index: null,
            is_tool_message: true, content: '', thinking_content: '',
            tool_call_ids: ['pending-call'], tool_return_ids: [],
        },
    ],
    tool_calls: [
        { tool_call_id: 'pending-call', tool_name: 'file_write', status: 'interrupted' },
    ],
});
assert.deepStrictEqual(
    rendered,
    [],
    'An incomplete tool-only tail must not create an empty completed assistant bubble.'
);
"""
    subprocess.run(
        ["node", "-e", harness, str(_HISTORY_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_session_map_timeline_prepends_older_canonical_rows_without_scroll_jump() -> (
    None
):
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Element {
    constructor(tag = 'div') {
        this.tag = tag;
        this.children = [];
        this.dataset = {};
        this.listeners = {};
        this.parentElement = null;
        this.disabled = false;
        this.textContent = '';
        this.scrollTop = 20;
    }
    set innerHTML(_value) { this.children = []; }
    get innerHTML() { return ''; }
    get scrollHeight() { return this.children.length * 10; }
    addEventListener(name, listener) { this.listeners[name] = listener; }
    setAttribute(name, value) { this[name] = value; }
    appendChild(child) {
        if (child.isFragment) {
            for (const nested of [...child.children]) this.appendChild(nested);
            return child;
        }
        child.remove();
        child.parentElement = this;
        this.children.push(child);
        return child;
    }
    insertBefore(child, anchor) {
        if (child.isFragment) {
            for (const nested of [...child.children]) this.insertBefore(nested, anchor);
            return child;
        }
        child.remove();
        child.parentElement = this;
        const index = anchor ? this.children.indexOf(anchor) : -1;
        if (index < 0) this.children.push(child);
        else this.children.splice(index, 0, child);
        return child;
    }
    prepend(child) { return this.insertBefore(child, this.children[0] || null); }
    remove() {
        if (!this.parentElement) return;
        const index = this.parentElement.children.indexOf(this);
        if (index >= 0) this.parentElement.children.splice(index, 1);
        this.parentElement = null;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    querySelectorAll(selector) {
        const descendants = [];
        const visit = node => {
            for (const child of node.children) {
                descendants.push(child);
                visit(child);
            }
        };
        visit(this);
        if (selector === '[data-session-map-context-boundary="true"]') {
            return descendants.filter(node => node.dataset.sessionMapContextBoundary === 'true');
        }
        if (selector === '[data-canonical-start]') {
            return descendants.filter(node => node.dataset.canonicalStart !== undefined);
        }
        if (selector === '[data-load-older-chat-messages]') {
            return descendants.filter(node => node.dataset.loadOlderChatMessages === 'true');
        }
        if (selector === '.message-error') return [];
        return [];
    }
}
class Fragment extends Element { constructor() { super('fragment'); this.isFragment = true; } }

global.window = global;
global.document = {
    createElement: tag => new Element(tag),
    createDocumentFragment: () => new Fragment(),
};
const container = new Element('main');
const state = { sessionId: 'session-1' };
const requests = [];
const assistantNodes = [];
const assistantTurns = [];
global.fetch = async url => {
    requests.push(url);
    return {
        ok: true,
        json: async () => ({
            session_id: 'session-1',
            messages: [
                { role: 'user', sequence_index: 1, content: 'one' },
                { role: 'user', sequence_index: 2, content: 'two' },
            ],
            tool_calls: [],
            has_older: false,
            older_before_sequence_index: null,
        }),
    };
};
global.console = { error() {} };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = ChatHistoryRendering.create({
    state,
    elements: { chatMessages: container, vaultSelector: { value: 'Vault' } },
    icons: { MAP_ICON_SVG: '' },
    toolDetails: { close() {}, setEntryTokenCount() {} },
    messageControls: {
        addMessage(_role, _content, options) {
            const node = new Element('message');
            node.sequenceIndex = options.sequenceIndex;
            container.appendChild(node);
            return node;
        },
    },
    callbacks: {
        renderEmptyState() {},
        openSessionMap() {},
        createAssistantStreamingMessage() {
            const messageDiv = new Element('message');
            const contentDiv = new Element('content');
            messageDiv.appendChild(contentDiv);
            container.appendChild(messageDiv);
            assistantNodes.push({ messageDiv, contentDiv });
            return { messageDiv, contentDiv, toolStatusMap: new Map() };
        },
        renderAssistantMarkdown() {},
        ensureToolCallsSection() {},
        createToolStatusEntry(context, toolId, payload) {
            const entry = { toolId, toolName: payload.tool_name, state: 'running' };
            context.toolStatusMap.set(toolId, entry);
            return entry;
        },
        setToolEntryState(entry, state) { entry.state = state; },
        updateToolCallsSummary() {},
        finalizeAssistantMessage(context) {
            assistantTurns.push({
                content: context.fullText,
                toolIds: Array.from(context.toolStatusMap.keys()),
            });
        },
    },
});

(async () => {
    controller.renderSession({
        session_id: 'session-1',
        context_checkpoint_kind: 'session_map',
        context_checkpoint_id: 'checkpoint-1',
        context_boundary_sequence_index: 5,
        messages: [
            {
                role: 'assistant', sequence_index: 3, through_sequence_index: 5,
                fork_sequence_index: 5, content: 'The result.',
                tool_call_ids: ['call-1'], tool_return_ids: ['call-1'],
                tool_calls: [{ tool_call_id: 'call-1', tool_name: 'search', status: 'completed' }],
            },
            { role: 'user', sequence_index: 6, content: 'six' },
        ],
        tool_calls: [],
        has_older_messages: true,
        older_before_sequence_index: 3,
    });
    const loadRow = container.querySelector('[data-load-older-chat-messages]');
    assert.ok(loadRow, 'The newest page should offer older history.');
    await loadRow.children[0].listeners.click();
    assert.match(requests[0], /before_sequence_index=3/);
    const ordered = container.children.map(node => {
        if (node.dataset.sessionMapContextBoundary === 'true') return 'boundary';
        return Number(node.dataset.canonicalStart);
    });
    assert.deepStrictEqual(ordered, [1, 2, 3, 'boundary', 6]);
    assert.strictEqual(assistantNodes.length, 1, 'One assistant turn should produce one bubble.');
    assert.deepStrictEqual(assistantTurns, [{ content: 'The result.', toolIds: ['call-1'] }]);
    assert.strictEqual(container.scrollTop, 30, 'Prepending must preserve the reading anchor.');
    assert.strictEqual(container.querySelector('[data-load-older-chat-messages]'), null);
})().catch(error => { process.stderr.write(String(error.stack || error)); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", harness, str(_HISTORY_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_terminal_stream_event_sets_live_fork_origin() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const context = {};
global.fetch = async () => ({
    ok: true,
    json: async () => ({
        available: true,
        latest_sequence: 1,
        events: [{
            event: 'done',
            sequence: 1,
            fork_sequence_index: 17,
            choices: [{ delta: {}, finish_reason: 'stop' }],
        }],
    }),
});
const controller = ChatTaskStream.create({
    state: {},
    chatRendering: {
        renderAssistantMarkdown() {},
        resetAssistantStream() {},
    },
    callbacks: {},
});

(async () => {
    await controller.hydrateReplaySnapshot('task', context, new AbortController());
    assert.strictEqual(context.sequenceIndex, 17);
})().catch((error) => {
    console.error(error);
    process.exit(1);
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_TASK_STREAM_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
