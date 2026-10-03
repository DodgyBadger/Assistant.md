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
