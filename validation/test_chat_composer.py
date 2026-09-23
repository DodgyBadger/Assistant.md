"""Frontend smoke tests for chat composer state and controls."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = _PROJECT_ROOT / "static/js/chat-composer.js"


def test_chat_composer_exposes_layout_and_attachment_controls() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const controller = ChatComposer.create({
    state: {},
    composeState: { pendingAttachments: [], popoverOpen: false },
    elements: {},
    utils: { escapeHtml(value) { return String(value); } },
    browserStorage: { getItem() { return null; }, setItem() {} },
});

for (const name of [
    'addPendingAttachments',
    'clearPendingAttachments',
    'createClientSessionId',
    'scrollChatToBottom',
    'setChatFocusMode',
]) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing chat composer method: ${name}`);
    }
}
if (!controller.createClientSessionId('Test Vault').startsWith('Test_Vault_')) {
    throw new Error('Client session IDs should include the normalized vault name.');
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE_PATH)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_chat_composer_loads_before_application_bootstrap() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    composer_position = markup.index(
        '<script src="static/js/chat-composer.js"></script>'
    )
    application_position = markup.index('<script src="static/app.js"></script>')

    assert composer_position < application_position
