"""Frontend smoke tests for the vault path picker module graph."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ACTIONS_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-actions.js"
_PICKER_MODULE = _PROJECT_ROOT / "static/js/vault-path-picker.js"


def test_vault_path_picker_composes_explorer_actions_controller() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

const controller = VaultPathPicker.create({
    elements: {},
    icons: {},
    utils: {
        escapeHtml(value) { return String(value); },
        flashCopyFeedback() {},
        handleCopy() {},
    },
});

for (const name of ['open', 'close', 'syncInteractionLocks']) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing vault path picker method: ${name}`);
    }
}
"""
    subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(_ACTIONS_MODULE),
            str(_PICKER_MODULE),
        ],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_vault_explorer_actions_load_before_path_picker() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    actions_position = markup.index(
        '<script src="static/js/vault-explorer-actions.js"></script>'
    )
    picker_position = markup.index(
        '<script src="static/js/vault-path-picker.js"></script>'
    )

    assert actions_position < picker_position
