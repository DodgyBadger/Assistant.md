"""Frontend contract tests for Vault Explorer batch Move composition."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-batch-moves.js"


def test_batch_move_controller_has_a_small_public_contract() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const controller = VaultExplorerBatchMoves.create({
    icons: {},
    utils: { escapeHtml(value) { return String(value); } },
    callbacks: {},
});
assert.deepStrictEqual(Object.keys(controller).sort(), ['show', 'submit']);
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
