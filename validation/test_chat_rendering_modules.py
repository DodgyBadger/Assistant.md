"""Frontend smoke tests for the chat rendering module graph."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DETAILS_MODULE = _PROJECT_ROOT / "static/js/chat-tool-details.js"
_MARKDOWN_MODULE = _PROJECT_ROOT / "static/js/chat-markdown.js"
_START_PANEL_MODULE = _PROJECT_ROOT / "static/js/chat-start-panel.js"
_THINKING_MODULE = _PROJECT_ROOT / "static/js/chat-thinking.js"
_RENDERING_MODULE = _PROJECT_ROOT / "static/js/chat-rendering.js"


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
        < start_panel_position
        < thinking_position
        < rendering_position
    )
