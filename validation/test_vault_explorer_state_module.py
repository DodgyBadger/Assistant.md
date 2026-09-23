"""Frontend contract tests for transient Vault Explorer state."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATE_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-state.js"


def test_vault_explorer_state_tracks_selection_and_operation_applicability() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const changes = [];
const state = VaultExplorerState.create({
    onChange(snapshot) { changes.push(snapshot); },
});

assert.deepStrictEqual(
    Object.keys(state).sort(),
    [
        'clearSelection',
        'deselect',
        'reset',
        'restore',
        'retainSelection',
        'select',
        'setActiveFolder',
        'setFeatures',
        'snapshot',
        'toggle',
    ]
);

let snapshot = state.snapshot();
assert.strictEqual(snapshot.activeFolder, '');
assert.strictEqual(snapshot.selectedCount, 0);
assert.strictEqual(snapshot.operations.new_file.enabled, true);
assert.strictEqual(snapshot.operations.upload.enabled, true);
assert.strictEqual(snapshot.operations.import_url.enabled, true);
assert.strictEqual(snapshot.operations.open.enabled, false);

state.setActiveFolder('/Projects//Current/');
snapshot = state.snapshot();
assert.strictEqual(snapshot.activeFolder, 'Projects/Current');
assert.strictEqual(snapshot.destinationPath, 'Projects/Current');

state.select({ path: 'Projects/Current/source.pdf', kind: 'file', importEligible: true });
snapshot = state.snapshot();
assert.strictEqual(snapshot.selectedCount, 1);
assert.deepStrictEqual(snapshot.selectedPaths, ['Projects/Current/source.pdf']);
assert.strictEqual(snapshot.operations.open.enabled, true);
assert.strictEqual(snapshot.operations.import_file.enabled, true);
assert.strictEqual(snapshot.operations.rename.enabled, true);
assert.strictEqual(snapshot.operations.upload.enabled, false);
assert.match(snapshot.operations.upload.reason, /folder/i);

state.toggle({ path: 'Projects/Current/source.pdf', kind: 'file' });
assert.strictEqual(state.snapshot().selectedCount, 0);

state.select({ path: 'Projects/Archive/', kind: 'directory' });
snapshot = state.snapshot();
assert.strictEqual(snapshot.destinationPath, 'Projects/Archive');
assert.strictEqual(snapshot.operations.new_file.enabled, true);
assert.strictEqual(snapshot.operations.upload.enabled, true);
assert.strictEqual(snapshot.operations.workspace.enabled, true);
assert.strictEqual(snapshot.operations.import_file.enabled, false);

state.select({ path: 'Projects/Current/second.pdf', kind: 'file', importEligible: true });
snapshot = state.snapshot();
assert.strictEqual(snapshot.selectedCount, 2);
assert.strictEqual(snapshot.operations.new_file.enabled, false);
assert.strictEqual(snapshot.operations.reference.enabled, true);
assert.strictEqual(snapshot.operations.move.enabled, false);
assert.match(snapshot.operations.move.reason, /unavailable/i);

state.setFeatures({ batchMove: true, batchDelete: false });
snapshot = state.snapshot();
assert.strictEqual(snapshot.operations.move.enabled, true);
assert.strictEqual(snapshot.operations.delete.enabled, false);

state.clearSelection();
state.select({ path: 'Projects', kind: 'directory' });
state.select({ path: 'Projects/Current/source.pdf', kind: 'file', importEligible: true });
snapshot = state.snapshot();
assert.strictEqual(snapshot.hasAncestorConflict, true);
assert.strictEqual(snapshot.operations.move.enabled, false);
assert.match(snapshot.operations.move.reason, /contains another selected item/i);

state.retainSelection(['Projects/Current/source.pdf', 'Missing.md']);
snapshot = state.snapshot();
assert.deepStrictEqual(snapshot.selectedPaths, ['Projects/Current/source.pdf']);
assert.strictEqual(snapshot.hasAncestorConflict, false);

const saved = snapshot;
state.reset({ activeFolder: 'Other' });
assert.strictEqual(state.snapshot().activeFolder, 'Other');
assert.strictEqual(state.snapshot().selectedCount, 0);
state.restore(saved);
assert.strictEqual(state.snapshot().activeFolder, 'Projects/Current');
assert.deepStrictEqual(state.snapshot().selectedPaths, ['Projects/Current/source.pdf']);

assert.ok(changes.length >= 10);
assert.throws(
    () => state.select({ path: '../outside.md', kind: 'file' }),
    /vault-relative/i
);
assert.throws(
    () => state.setActiveFolder('Projects/../outside'),
    /vault-relative/i
);
"""
    subprocess.run(
        ["node", "-e", harness, str(_STATE_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_vault_explorer_state_loads_before_picker() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    state_position = markup.index(
        '<script src="static/js/vault-explorer-state.js"></script>'
    )
    picker_position = markup.index(
        '<script src="static/js/vault-path-picker.js"></script>'
    )

    assert state_position < picker_position
