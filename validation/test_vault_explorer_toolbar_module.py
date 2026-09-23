"""Frontend contract tests for the Vault Explorer operation bar."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TOOLBAR_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-toolbar.js"


def test_vault_explorer_toolbar_renders_and_dispatches_applicable_actions() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const handlers = {};
const container = {
    innerHTML: '',
    addEventListener(name, handler) { handlers[name] = handler; },
    removeEventListener(name, handler) {
        if (handlers[name] === handler) delete handlers[name];
    },
};
const dispatched = [];
const toolbar = VaultExplorerToolbar.create({
    utils: {
        escapeHtml(value) {
            return String(value)
                .replaceAll('&', '&amp;')
                .replaceAll('<', '&lt;')
                .replaceAll('>', '&gt;')
                .replaceAll('"', '&quot;');
        },
    },
    callbacks: {
        onAction(action, snapshot) {
            dispatched.push({ action, count: snapshot.selectedCount });
        },
        onLocation(path) {
            dispatched.push({ location: path });
        },
    },
});

assert.deepStrictEqual(
    Object.keys(toolbar).sort(),
    ['destroy', 'mount', 'render']
);
toolbar.mount(container);

const noSelection = {
    activeFolder: 'Projects & Notes',
    destinationPath: 'Projects & Notes',
    selectedCount: 0,
    selectedItems: [],
    operations: {
        new_file: { enabled: true, reason: '', destinationPath: 'Projects & Notes' },
        new_directory: { enabled: true, reason: '', destinationPath: 'Projects & Notes' },
        upload: { enabled: true, reason: '', destinationPath: 'Projects & Notes' },
        import_url: { enabled: true, reason: '', destinationPath: 'Projects & Notes' },
        refresh: { enabled: true, reason: '' },
        rename: { enabled: false, reason: 'Select one item.' },
        clear: { enabled: false, reason: 'Nothing is selected.' },
    },
};
toolbar.render(noSelection, { supportedActions: ['new_file', 'new_directory', 'upload', 'refresh', 'rename'] });
assert.match(container.innerHTML, /Projects &amp; Notes/);
assert.match(container.innerHTML, /data-vault-explorer-location=""/);
assert.match(container.innerHTML, /data-vault-explorer-location="Projects &amp; Notes"/);
assert.match(container.innerHTML, /New file/);
assert.match(container.innerHTML, /New folder/);
assert.match(container.innerHTML, /Upload/);
assert.match(container.innerHTML, /Refresh/);
assert.doesNotMatch(container.innerHTML, /Rename/);
assert.doesNotMatch(container.innerHTML, /Import URL/);
assert.doesNotMatch(container.innerHTML, /Clear selection/);

const selectedFile = {
    activeFolder: 'Projects',
    destinationPath: '',
    selectedCount: 1,
    selectedItems: [{ path: 'Projects/report.md', kind: 'file' }],
    operations: {
        open: { enabled: true, reason: '' },
        reference: { enabled: true, reason: '' },
        copy: { enabled: true, reason: '' },
        rename: { enabled: true, reason: '' },
        move: { enabled: true, reason: '' },
        delete: { enabled: true, reason: '' },
        clear: { enabled: true, reason: '' },
    },
};
toolbar.render(selectedFile, {
    readOnly: true,
    lockMessage: 'Wait for the response.',
    supportedActions: ['open', 'reference', 'copy', 'rename', 'move', 'delete'],
});
assert.match(container.innerHTML, /1 selected/);
assert.match(container.innerHTML, /Open/);
assert.match(container.innerHTML, /Add to prompt/);
assert.match(container.innerHTML, /Clear selection/);
assert.match(container.innerHTML, /data-vault-explorer-toolbar-action="reference"[^>]*disabled/);
assert.match(container.innerHTML, /data-vault-explorer-toolbar-action="copy"/);

const openButton = {
    disabled: false,
    getAttribute(name) {
        return name === 'data-vault-explorer-toolbar-action' ? 'open' : null;
    },
};
handlers.click({ target: { closest() { return openButton; } } });
assert.deepStrictEqual(dispatched, [{ action: 'open', count: 1 }]);

const lockedButton = {
    disabled: true,
    getAttribute() { return 'reference'; },
};
handlers.click({ target: { closest() { return lockedButton; } } });
assert.strictEqual(dispatched.length, 1);

const rootButton = {
    getAttribute(name) {
        return name === 'data-vault-explorer-location' ? '' : null;
    },
};
handlers.click({
    target: {
        closest(selector) {
            return selector === '[data-vault-explorer-location]' ? rootButton : null;
        },
    },
});
assert.deepStrictEqual(dispatched[1], { location: '' });

toolbar.destroy();
assert.strictEqual(handlers.click, undefined);
assert.strictEqual(container.innerHTML, '');
"""
    subprocess.run(
        ["node", "-e", harness, str(_TOOLBAR_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_vault_explorer_toolbar_loads_before_picker() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    toolbar_position = markup.index(
        '<script src="static/js/vault-explorer-toolbar.js"></script>'
    )
    picker_position = markup.index(
        '<script src="static/js/vault-path-picker.js"></script>'
    )

    assert toolbar_position < picker_position
