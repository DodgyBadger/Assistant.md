"""Frontend smoke tests for chat selection coordination."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/chat-selection.js"


def test_chat_selection_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = ChatSelection.create({
    state: {},
    elements: {},
    sessionControls: {},
    callbacks: {},
});

for (const name of [
    'fetchMetadata',
    'populateSelectors',
    'resetChatModeToDefault',
    'isChatSelectableModel',
    'persistSelectedChatMode',
    'handleVaultChange',
    'fetchTemplates',
]) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing chat selection method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_chat_selection_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    selection_position = markup.index(
        '<script src="static/js/chat-selection.js"></script>'
    )
    application_position = markup.index('<script src="static/app.js"></script>')

    assert selection_position < application_position
