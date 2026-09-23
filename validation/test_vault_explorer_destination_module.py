"""Frontend contract tests for Vault Explorer destination selection."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DESTINATION_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-destination.js"


def test_destination_mode_normalizes_paths_and_rejects_self_moves() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const changes = [];
const destination = VaultExplorerDestination.create({
    onChange(snapshot) { changes.push(snapshot); },
});
assert.deepStrictEqual(
    Object.keys(destination).sort(),
    ['begin', 'cancel', 'select', 'snapshot']
);
destination.begin({
    purpose: 'move',
    initialPath: 'Projects//Archive/',
    sourcePath: 'Projects/Notes',
    sourceKind: 'directory',
});
assert.strictEqual(destination.snapshot().path, 'Projects/Archive');
destination.select('');
assert.strictEqual(destination.snapshot().path, '');
assert.throws(
    () => destination.select('Projects/Notes/Child'),
    /cannot be moved into itself/
);
destination.cancel();
assert.strictEqual(destination.snapshot().active, false);
assert.strictEqual(changes.length, 3);
"""
    subprocess.run(
        ["node", "-e", harness, str(_DESTINATION_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
