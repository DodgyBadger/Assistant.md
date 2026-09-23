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
const locationHandlers = {};
const selectionHandlers = {};
const createOptions = { hidden: true };
const importOptions = { hidden: true };
const createToggle = {
    disabled: false,
    expanded: 'false',
    setAttribute(name, value) {
        if (name === 'aria-expanded') this.expanded = value;
    },
    getAttribute(name) {
        return name === 'data-vault-explorer-action-menu-toggle' ? 'create' : null;
    },
};
const importToggle = {
    disabled: false,
    expanded: 'false',
    setAttribute(name, value) {
        if (name === 'aria-expanded') this.expanded = value;
    },
    getAttribute(name) {
        return name === 'data-vault-explorer-action-menu-toggle' ? 'import' : null;
    },
};
const container = {
    innerHTML: '',
    addEventListener(name, handler) { handlers[name] = handler; },
    removeEventListener(name, handler) {
        if (handlers[name] === handler) delete handlers[name];
    },
    querySelector(selector) {
        if (selector === '[data-vault-explorer-action-menu-options="create"]') return createOptions;
        if (selector === '[data-vault-explorer-action-menu-options="import"]') return importOptions;
        return null;
    },
    querySelectorAll(selector) {
        if (selector === '[data-vault-explorer-action-menu-options]') {
            return [createOptions, importOptions];
        }
        if (selector === '[data-vault-explorer-action-menu-toggle]') {
            return [createToggle, importToggle];
        }
        return [];
    },
};
const locationContainer = {
    innerHTML: '',
    addEventListener(name, handler) { locationHandlers[name] = handler; },
    removeEventListener(name, handler) {
        if (locationHandlers[name] === handler) delete locationHandlers[name];
    },
};
const selectionContainer = {
    innerHTML: '',
    addEventListener(name, handler) { selectionHandlers[name] = handler; },
    removeEventListener(name, handler) {
        if (selectionHandlers[name] === handler) delete selectionHandlers[name];
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
};
const dispatched = [];
const toolbar = VaultExplorerToolbar.create({
    icons: {
        PLUS_ICON_SVG: '<svg data-test-icon="plus"></svg>',
        SLASH_ICON_SVG: '<svg data-test-icon="slash"></svg>',
        FOLDER_ICON_SVG: '<svg data-test-icon="folder"></svg>',
        UPLOAD_ICON_SVG: '<svg data-test-icon="upload"></svg>',
        REFRESH_ICON_SVG: '<svg data-test-icon="refresh"></svg>',
        EYE_ICON_SVG: '<svg data-test-icon="eye"></svg>',
        MESSAGE_SQUARE_PLUS_ICON_SVG: '<svg data-test-icon="message-square-plus"></svg>',
        CLIPBOARD_COPY_ICON_SVG: '<svg data-test-icon="clipboard-copy"></svg>',
        IMPORT_ICON_SVG: '<svg data-test-icon="import"></svg>',
        LINK_ICON_SVG: '<svg data-test-icon="link"></svg>',
        FILE_DOWN_ICON_SVG: '<svg data-test-icon="file-down"></svg>',
        BRIEFCASE_BUSINESS_ICON_SVG: '<svg data-test-icon="briefcase-business"></svg>',
        MOVE_ICON_SVG: '<svg data-test-icon="move"></svg>',
        X_ICON_SVG: '<svg data-test-icon="clear"></svg>',
    },
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
toolbar.mount(container, locationContainer, selectionContainer);

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
        import_file: { enabled: false, reason: 'Select a supported file.' },
        refresh: { enabled: true, reason: '' },
        rename: { enabled: false, reason: 'Select one item.' },
        clear: { enabled: false, reason: 'Nothing is selected.' },
    },
};
toolbar.render(noSelection, {
    supportedActions: ['new_file', 'new_directory', 'upload', 'import_url', 'import_file', 'refresh', 'rename'],
    vaultName: 'Personal',
    workspacePath: 'Workspace',
});
assert.strictEqual(selectionContainer.innerHTML, '');
assert.match(locationContainer.innerHTML, /Personal:/);
assert.match(locationContainer.innerHTML, /Projects &amp; Notes/);
assert.match(locationContainer.innerHTML, /data-vault-explorer-location=""/);
assert.match(locationContainer.innerHTML, /data-vault-explorer-location="Projects &amp; Notes"/);
assert.match(locationContainer.innerHTML, /data-vault-explorer-location="Workspace"/);
assert.match(locationContainer.innerHTML, /data-test-icon="slash"/);
assert.match(container.innerHTML, /New file/);
assert.match(container.innerHTML, /New folder/);
assert.match(container.innerHTML, /Upload/);
assert.match(container.innerHTML, /Refresh/);
assert.match(container.innerHTML, /Import URL/);
assert.match(container.innerHTML, /Import to Markdown/);
assert.match(container.innerHTML, /data-vault-explorer-action-menu-toggle="import"[\s\S]*?data-test-icon="import"/);
assert.match(container.innerHTML, /data-vault-explorer-toolbar-action="import_url"[\s\S]*?data-test-icon="link"/);
assert.match(container.innerHTML, /data-vault-explorer-toolbar-action="import_file"[^>]*disabled/);
assert.match(container.innerHTML, /data-test-icon="plus"/);
assert.match(container.innerHTML, /data-test-icon="folder"/);
assert.match(container.innerHTML, /data-test-icon="upload"/);
assert.match(container.innerHTML, /data-test-icon="refresh"/);
assert.match(container.innerHTML, /data-vault-explorer-action-menu-toggle="create"/);
assert.match(container.innerHTML, /data-vault-explorer-action-menu-options="create"/);
assert.doesNotMatch(container.innerHTML, /class="[^\"]*vault-explorer-toolbar-action[^\"]*"[^>]*data-vault-explorer-toolbar-action="new_file"/);
assert.doesNotMatch(container.innerHTML, /Rename/);
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
        import_file: { enabled: true, reason: '' },
        workspace: { enabled: true, reason: '' },
        rename: { enabled: true, reason: '' },
        move: { enabled: true, reason: '' },
        delete: { enabled: true, reason: '' },
        clear: { enabled: true, reason: '' },
    },
};
toolbar.render(selectedFile, {
    readOnly: true,
    lockMessage: 'Wait for the response.',
    supportedActions: ['open', 'reference', 'copy', 'import_file', 'workspace', 'rename', 'move', 'delete'],
});
assert.match(selectionContainer.innerHTML, /aria-label="1 selected"/);
assert.match(selectionContainer.innerHTML, />1 selected<\/button>/);
assert.match(container.innerHTML, /Open/);
assert.match(container.innerHTML, /Add to prompt/);
assert.match(selectionContainer.innerHTML, /Show selected/);
assert.match(selectionContainer.innerHTML, /Deselect all/);
assert.match(container.innerHTML, /data-test-icon="eye"/);
assert.match(container.innerHTML, /data-test-icon="message-square-plus"/);
assert.match(container.innerHTML, /data-test-icon="clipboard-copy"/);
assert.match(container.innerHTML, /data-test-icon="import"/);
assert.match(container.innerHTML, /data-test-icon="briefcase-business"/);
assert.match(container.innerHTML, /data-test-icon="move"/);
assert.match(container.innerHTML, /data-vault-explorer-toolbar-action="reference"[^>]*disabled/);
assert.match(container.innerHTML, /data-vault-explorer-toolbar-action="copy"/);

handlers.click({
    target: {
        closest(selector) {
            return selector === '[data-vault-explorer-action-menu-toggle]' ? importToggle : null;
        },
    },
});
assert.strictEqual(importOptions.hidden, false);
assert.strictEqual(importToggle.expanded, 'true');

handlers.keydown({ key: 'Escape' });
assert.strictEqual(importOptions.hidden, true);
assert.strictEqual(importToggle.expanded, 'false');

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
assert.strictEqual(handlers.keydown, undefined);
assert.strictEqual(locationHandlers.click, undefined);
assert.strictEqual(selectionHandlers.click, undefined);
assert.strictEqual(selectionHandlers.keydown, undefined);
assert.strictEqual(container.innerHTML, '');
assert.strictEqual(locationContainer.innerHTML, '');
assert.strictEqual(selectionContainer.innerHTML, '');
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
