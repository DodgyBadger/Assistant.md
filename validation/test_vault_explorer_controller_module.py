"""Frontend contract tests for Vault Explorer module composition."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATE_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-state.js"
_TOOLBAR_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-toolbar.js"
_CONTROLLER_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-controller.js"


def test_vault_explorer_controller_owns_selection_lifecycle() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

const controller = VaultExplorerController.create({
    utils: {
        escapeHtml(value) { return String(value); },
        flashCopyFeedback() {},
        handleCopy() {},
    },
    callbacks: {
        expandDirectory() {},
        handleMutationAction() {},
        isBusy() { return false; },
        isReadOnly() { return false; },
        refreshExplorer() {},
        setStatus() {},
        supportsImportPath(path) { return path.endsWith('.pdf'); },
    },
});

assert.deepStrictEqual(
    Object.keys(controller).sort(),
    ['batchMutationCompleted', 'close', 'mutationCompleted', 'open', 'render', 'setActiveFolder', 'snapshot', 'toggleSelection']
);
controller.setActiveFolder('Projects');
controller.toggleSelection({ path: 'Projects/old.md', kind: 'file' });
controller.mutationCompleted({
    operation: 'rename',
    sourcePath: 'Projects/old.md',
    targetPath: 'Projects/new.md',
    kind: 'file',
});
assert.strictEqual(controller.snapshot().activeFolder, 'Projects');
assert.deepStrictEqual(controller.snapshot().selectedPaths, ['Projects/new.md']);
controller.mutationCompleted({
    operation: 'delete',
    sourcePath: 'Projects/new.md',
    targetPath: 'Projects/new.md',
    kind: 'file',
});
assert.strictEqual(controller.snapshot().selectedCount, 0);
"""
    subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(_STATE_MODULE),
            str(_TOOLBAR_MODULE),
            str(_CONTROLLER_MODULE),
        ],
        check=True,
        cwd=_PROJECT_ROOT,
    )
