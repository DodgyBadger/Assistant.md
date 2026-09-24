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
    assert 'aria-level="${depth + 1}"' in source
    assert (
        source.index("data-vault-explorer-header-location")
        < source.index("vault-explorer-search-control")
        < source.index("data-vault-explorer-toolbar")
    )
    assert "data-vault-explorer-select-item" in source
    assert "data-vault-explorer-row-menu" not in source
    assert "data-vault-explorer-more" not in source
