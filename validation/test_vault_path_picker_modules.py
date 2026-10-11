"""Frontend smoke tests for the vault path picker module graph."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATE_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-state.js"
_TOOLBAR_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-toolbar.js"
_DESTINATION_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-destination.js"
_IMPORT_OPTIONS_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-import-options.js"
_IMPORTS_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-imports.js"
_ACTIONS_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-actions.js"
_BATCH_MOVES_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-batch-moves.js"
_SEARCH_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-search.js"
_CONTROLLER_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-controller.js"
_PICKER_MODULE = _PROJECT_ROOT / "static/js/vault-path-picker.js"


def test_refresh_preserves_expanded_tree_selection_and_scroll() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
global.window = global;
let scrollCalls = 0;
class Element {
    constructor() {
        this.dataset = {}; this.attrs = {}; this.isConnected = true;
        this.offsetParent = {}; this.scrollTop = 0; this.rows = [];
        this.classList = { add() {}, remove() {}, toggle() {} };
    }
    addEventListener() {}
    focus() {}
    remove() { this.isConnected = false; }
    setAttribute(k, v) { this.attrs[k] = v; }
    getAttribute(k) { return this.attrs[k]; }
    closest() { return this.parent; }
    scrollIntoView() { scrollCalls += 1; }
    set innerHTML(value) {
        this.html = value; this.scrollTop = 0;
        this.rows = [...value.matchAll(/data-vault-path-picker-row="([^"]+)"/g)].map((match) => makeRow(match[1]));
    }
    get innerHTML() { return this.html || ''; }
    querySelector(s) {
        if (s.includes('data-vault-path-picker-children')) return this.children || null;
        if (s.includes('data-vault-path-picker-toggle')) return this.toggle || null;
        if (s === ':scope > .workspace-tree-row') return this;
        return null;
    }
    querySelectorAll(s) {
        const rows = this.rows.flatMap((row) => [row, ...row.children.querySelectorAll('[data-vault-path-picker-row]')]);
        if (s === '[data-vault-path-picker-row]') return rows;
        if (s.includes('data-vault-path-picker-toggle')) return rows.map((r) => r.toggle).filter((t) => t && t.attrs['aria-expanded'] === 'true');
        return [];
    }
}
function makeRow(path) {
    const row = new Element(); row.attrs['data-vault-path-picker-row'] = path;
    row.children = new Element();
    if (!path.endsWith('.md')) {
        row.toggle = new Element(); row.toggle.parent = row;
        row.toggle.attrs['aria-expanded'] = 'false';
    }
    return row;
}
global.HTMLElement = Element;
global.HTMLInputElement = class extends Element {};
global.HTMLSelectElement = class extends Element {};
global.HTMLButtonElement = class extends Element {};
const query = new HTMLInputElement(); query.value = '';
const scope = new HTMLSelectElement(); scope.value = 'vault';
const mode = new HTMLInputElement(); mode.value = 'name';
const results = new Element(); const status = new Element();
let overlay;
global.document = {
    activeElement: null,
    getElementById(id) { return overlay?.isConnected && overlay.id === id ? overlay : null; },
    createElement() {
        overlay = new Element();
        overlay.querySelector = (s) => ({
            '[data-vault-path-picker-query]': query,
            '[data-vault-path-picker-scope]': scope,
            '[data-vault-path-picker-search-mode]': mode,
            '[data-vault-path-picker-results]': results,
            '[data-vault-path-picker-status]': status,
        }[s] || null);
        overlay.querySelectorAll = (s) => results.querySelectorAll(s);
        return overlay;
    },
    body: { appendChild() {} },
};
let refresh;
let snapshot = { activeFolder: 'Projects', selectedPaths: ['Projects/A/selected.md'] };
const empty = { create() { return { reset() {}, cancel() {}, isBusy() { return false; }, syncSubmitState() {}, syncDestinationSelection() {} }; }, supportsPath() { return true; } };
for (const name of ['VaultExplorerSearch', 'VaultExplorerActions', 'VaultExplorerImports', 'VaultExplorerBatchMoves']) global[name] = empty;
let contentLoads = 0;
global.VaultExplorerSearch = { create() { return {
    cancel() {}, async load({query: value}) { contentLoads += 1; assert.strictEqual(value, 'needle'); results.innerHTML = '<p>Content results</p>'; },
}; }};
global.VaultExplorerController = { create({callbacks}) {
    refresh = callbacks.refreshExplorer;
    return { open() {}, close() {}, render() {}, snapshot() { return snapshot; } };
}};
const requests = [];
global.fetch = async (url) => {
    const params = new URL(url, 'http://fixture/').searchParams;
    requests.push(params);
    const path = params.get('path');
    return { ok: true, async json() {
        return { items: path === 'Projects/A'
            ? [{path: 'Projects/A/selected.md', kind: 'file'}]
            : [{path: 'Projects/A', kind: 'directory', has_children: true}, {path: 'Projects/B', kind: 'directory', has_children: true}] };
    }};
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const picker = VaultPathPicker.create({ elements: {vaultSelector: {value: 'Test'}}, icons: {}, utils: {escapeHtml: String} });
(async () => {
    picker.open({explorer: true, initialScope: 'vault'});
    await new Promise(setImmediate);
    results.rows[0].toggle.attrs['aria-expanded'] = 'true';
    results.rows[0].children.innerHTML = '<div data-vault-path-picker-row="Projects/A/selected.md"></div>';
    results.scrollTop = 240;
    query.value = 'selected'; scope.value = 'workspace';
    await refresh(overlay, {explorer: true});
    assert.strictEqual(query.value, 'selected');
    assert.strictEqual(scope.value, 'workspace');
    assert.strictEqual(requests.at(-2).get('path'), 'Projects');
    assert.strictEqual(requests.at(-2).get('query'), 'selected');
    assert.strictEqual(results.rows[0].toggle.attrs['aria-expanded'], 'true');
    assert.strictEqual(results.rows[1].toggle.attrs['aria-expanded'], 'false');
    assert.strictEqual(results.rows[0].children.rows[0].getAttribute('data-vault-path-picker-row'), 'Projects/A/selected.md');
    assert.match(results.rows[0].children.innerHTML, /is-selected/);
    assert.deepStrictEqual(snapshot.selectedPaths, ['Projects/A/selected.md']);
    assert.strictEqual(results.scrollTop, 240);
    assert.strictEqual(scrollCalls, 0);
    // Removed branches are skipped safely; explicit mutation reveal clears the name query.
    snapshot = {activeFolder: 'Projects', selectedPaths: []};
    results.rows[0].attrs['data-vault-path-picker-row'] = 'Projects/Removed';
    mode.value = 'content';
    await refresh(overlay, {explorer: true}, 'Projects/A/selected.md');
    assert.strictEqual(query.value, '');
    assert.strictEqual(scope.value, 'vault');
    assert.strictEqual(mode.value, 'name');
    assert.strictEqual(scrollCalls, 1);
    mode.value = 'content'; query.value = 'needle'; results.scrollTop = 80;
    await refresh(overlay, {explorer: true});
    assert.strictEqual(contentLoads, 1);
    assert.strictEqual(mode.value, 'content');
    assert.strictEqual(query.value, 'needle');
    assert.strictEqual(results.scrollTop, 80);
    // A newer refresh wins over a slow earlier root response and its scroll snapshot.
    mode.value = 'name'; query.value = '';
    const originalFetch = fetch;
    let resolveSlow;
    global.fetch = () => new Promise((resolve) => { resolveSlow = resolve; });
    results.scrollTop = 300;
    const slow = refresh(overlay, {explorer: true});
    global.fetch = originalFetch;
    results.scrollTop = 60;
    await refresh(overlay, {explorer: true});
    resolveSlow({ok: true, async json() { return {items: []}; }});
    await slow;
    assert.strictEqual(results.scrollTop, 60);
    assert.strictEqual(results.rows.length, 2);
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    subprocess.run(
        ["node", "-e", harness, str(_PICKER_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


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
            str(_STATE_MODULE),
            str(_TOOLBAR_MODULE),
            str(_DESTINATION_MODULE),
            str(_IMPORT_OPTIONS_MODULE),
            str(_IMPORTS_MODULE),
            str(_ACTIONS_MODULE),
            str(_BATCH_MOVES_MODULE),
            str(_SEARCH_MODULE),
            str(_CONTROLLER_MODULE),
            str(_PICKER_MODULE),
        ],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_vault_explorer_actions_reject_repeated_mutation_submission() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.HTMLElement = class HTMLElement {};
global.HTMLButtonElement = class HTMLButtonElement extends HTMLElement {
    constructor() { super(); this.disabled = false; }
};
global.HTMLInputElement = class HTMLInputElement extends HTMLElement {};
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

let resolveMutation;
let mutationCalls = 0;
const pendingMutation = new Promise((resolve) => { resolveMutation = resolve; });
const submit = new HTMLButtonElement();
const status = { textContent: '', innerHTML: '' };
const form = {
    dataset: { operation: 'delete', path: 'Notes/a.md', parent: 'Notes', kind: 'file' },
    elements: { namedItem() { return null; } },
    querySelector(selector) {
        if (selector === 'button[type="submit"]') return submit;
        if (selector === '[data-vault-explorer-form-status]') return status;
        return null;
    },
};
const overlay = { querySelector() { return null; }, querySelectorAll() { return []; } };
const controller = VaultExplorerActions.create({
    icons: {},
    utils: { escapeHtml(value) { return String(value); } },
    callbacks: {
        isReadOnly() { return false; },
        isActiveOverlay() { return true; },
        mutationCompleted() {},
        refreshExplorer() { return Promise.resolve(); },
        setStatus() {},
        syncInteractionLocks() {},
        workspacePath() { return ''; },
    },
});
const options = {
    onMutate() {
        mutationCalls += 1;
        return pendingMutation;
    },
};

const first = controller.submitMutation(overlay, form, options);
const second = controller.submitMutation(overlay, form, options);
assert.strictEqual(mutationCalls, 1);
resolveMutation({ path: 'Notes/a.md' });
Promise.all([first, second]).then(() => {
    assert.strictEqual(mutationCalls, 1);
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_DESTINATION_MODULE), str(_ACTIONS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_vault_explorer_modules_load_before_path_picker() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    state_position = markup.index(
        '<script src="static/js/vault-explorer-state.js"></script>'
    )
    toolbar_position = markup.index(
        '<script src="static/js/vault-explorer-toolbar.js"></script>'
    )
    destination_position = markup.index(
        '<script src="static/js/vault-explorer-destination.js"></script>'
    )
    imports_position = markup.index(
        '<script src="static/js/vault-explorer-imports.js"></script>'
    )
    import_options_position = markup.index(
        '<script src="static/js/vault-explorer-import-options.js"></script>'
    )
    actions_position = markup.index(
        '<script src="static/js/vault-explorer-actions.js"></script>'
    )
    batch_moves_position = markup.index(
        '<script src="static/js/vault-explorer-batch-moves.js"></script>'
    )
    search_position = markup.index(
        '<script src="static/js/vault-explorer-search.js"></script>'
    )
    controller_position = markup.index(
        '<script src="static/js/vault-explorer-controller.js"></script>'
    )
    picker_position = markup.index(
        '<script src="static/js/vault-path-picker.js"></script>'
    )

    assert (
        state_position
        < toolbar_position
        < destination_position
        < import_options_position
        < imports_position
        < actions_position
        < batch_moves_position
        < search_position
        < controller_position
        < picker_position
    )


def test_vault_explorer_uses_selection_toolbar_instead_of_row_action_menus() -> None:
    source = _PICKER_MODULE.read_text(encoding="utf-8")

    assert "data-vault-explorer-toolbar" in source
    assert "data-vault-explorer-header-location" in source
    assert "vault-path-picker-body" in source
    assert "vault-explorer-search-control" in source
    assert 'data-vault-explorer-tree="expand"' in source
    assert 'data-vault-explorer-tree="collapse"' in source
    assert "data-vault-explorer-descendant-selection" in source
    assert "visibleTreeRows" in source
    assert "event.key === 'ArrowDown'" in source
    assert "event.key === 'ArrowRight'" in source
    assert source.count("event.stopPropagation();") >= 2
    assert "await loadMoreResults(more, options)" in source
    assert "treeLifecycleGeneration" in source
    assert "expandAllGeneration" in source
    assert "childLoadGenerations" in source
    assert "explorerActions.syncSubmitState(overlay, readOnly)" in source
    assert "details:not([open])" in source
    assert "?.setAttribute('aria-expanded', 'false')" in source
    assert 'aria-level="${depth + 1}"' in source
    assert (
        source.index("data-vault-explorer-header-location")
        < source.index("vault-explorer-search-control")
        < source.index("data-vault-explorer-toolbar")
    )
    assert "data-vault-explorer-select-item" in source
    assert "data-vault-explorer-row-menu" not in source
    assert "data-vault-explorer-more" not in source
