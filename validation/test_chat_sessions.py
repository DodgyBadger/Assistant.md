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


def test_chat_sessions_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    sessions_position = markup.index(
        '<script src="static/js/chat-sessions.js"></script>'
    )
    application_position = markup.index('<script src="static/app.js"></script>')

    assert sessions_position < application_position
