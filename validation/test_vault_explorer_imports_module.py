"""Frontend contract tests for Vault Explorer import orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_IMPORTS_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-imports.js"


def test_vault_explorer_imports_exposes_bounded_controller_and_file_support() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

assert.strictEqual(VaultExplorerImports.supportsPath('Uploads/report.PDF'), true);
assert.strictEqual(VaultExplorerImports.supportsPath('Uploads/scan.webp'), true);
assert.strictEqual(VaultExplorerImports.supportsPath('Uploads/notes.txt'), false);

const controller = VaultExplorerImports.create({
    utils: { escapeHtml(value) { return String(value); } },
    callbacks: {
        closeActionPanel() {},
        refreshExplorer() {},
        syncInteractionLocks() {},
    },
});
assert.deepStrictEqual(
    Object.keys(controller).sort(),
    ['isBusy', 'reset', 'showFiles', 'showUrl', 'submit']
);
assert.strictEqual(controller.isBusy(), false);
"""
    subprocess.run(
        ["node", "-e", harness, str(_IMPORTS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_vault_explorer_import_forms_use_compact_progressive_layout() -> None:
    source = _IMPORTS_MODULE.read_text(encoding="utf-8")

    assert "vault-explorer-import-primary" in source
    assert "vault-explorer-import-source-count" in source
    assert "sources.map" not in source
    assert "vault-explorer-import-footer" in source
    assert "vault-explorer-import-options-grid" in source
    assert '<details class="vault-explorer-import-advanced">' in source
    assert "vault-explorer-action-header" not in source
