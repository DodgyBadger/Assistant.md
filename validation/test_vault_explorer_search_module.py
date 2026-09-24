"""Frontend contract tests for Vault Explorer content search."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_SEARCH_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-search.js"


def test_vault_explorer_search_renders_structured_escaped_matches() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const controller = VaultExplorerSearch.create({
    utils: {
        escapeHtml(value) {
            return String(value)
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;');
        },
    },
});
assert.deepStrictEqual(
    Object.keys(controller).sort(),
    ['cancel', 'load', 'render', 'search']
);

const rendered = controller.render({
    matches: [{
        path: 'Notes/<unsafe>.md',
        line: 7,
        column: 4,
        snippet: 'A <script> & more',
    }, {
        path: 'Notes/<unsafe>.md',
        line: 11,
        column: 2,
        snippet: 'Another match',
    }],
}, {
    selectedPaths: ['Notes/<unsafe>.md'],
    supportsPath(path) { return path.endsWith('.md'); },
});

assert.match(rendered, /Notes\/&lt;unsafe&gt;\.md/);
assert.match(rendered, /Line 7 · A &lt;script&gt; &amp; more/);
assert.match(rendered, /Line 11 · Another match/);
assert.match(rendered, /data-import-eligible="true"/);
assert.match(rendered, /is-selected/);
assert.doesNotMatch(rendered, /<script>/);
assert.strictEqual((rendered.match(/data-vault-path-picker-row=/g) || []).length, 1);

const pickerRendered = controller.render({
    matches: [{ path: 'Notes/result.md', line: 1, column: 1, snippet: 'Match' }],
}, {
    selectable: false,
    supportsPath() { return true; },
});
assert.doesNotMatch(pickerRendered, /data-vault-explorer-select-item/);
"""
    subprocess.run(
        ["node", "-e", harness, str(_SEARCH_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
