"""Frontend contract tests for Vault Explorer import orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_IMPORT_OPTIONS_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-import-options.js"
_IMPORTS_MODULE = _PROJECT_ROOT / "static/js/vault-explorer-imports.js"


def test_vault_explorer_imports_exposes_bounded_controller_and_file_support() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.App = { metadata: { ingestion_capabilities: { file_import: {
    features: ['.jpeg', '.jpg', '.pdf', '.png', '.tif', '.tiff', '.webp'],
} } } };
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

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
    ['isBusy', 'reset', 'showFiles', 'showUrl', 'submit', 'updateDestination']
);
assert.strictEqual(controller.isBusy(), false);

global.HTMLElement = class HTMLElement {};
global.HTMLInputElement = class HTMLInputElement extends HTMLElement {};
global.HTMLSelectElement = class HTMLSelectElement extends HTMLElement {};
global.HTMLFormElement = class HTMLFormElement extends HTMLElement {
    constructor() {
        super();
        this.dataset = {};
        this.elements = { namedItem() { return null; } };
    }
    addEventListener() {}
    querySelector(selector) {
        if (selector === '[data-vault-explorer-import-options-summary]') {
            return { textContent: '' };
        }
        return null;
    }
    querySelectorAll() { return []; }
};
const form = new HTMLFormElement();
const panel = new HTMLElement();
panel.dataset = {};
panel.classList = { remove() {} };
panel.querySelector = (selector) => (
    selector === '[data-vault-explorer-import-form]' ? form : null
);
const overlay = {
    querySelector(selector) {
        return selector === '[data-vault-explorer-action-panel]' ? panel : null;
    },
};
assert.doesNotThrow(() => controller.showUrl(overlay, { destination: '' }, {
    onImportSources() {},
}));
assert.match(panel.innerHTML, /Document URL/);
assert.match(panel.innerHTML, /Vault root/);

const roundTripForm = new HTMLFormElement();
const controls = {
    queue_only: Object.assign(new HTMLInputElement(), { type: 'checkbox', checked: false }),
    pdf_mode: Object.assign(new HTMLSelectElement(), { value: '' }),
    pdf_strategy: Object.assign(new HTMLSelectElement(), { value: '' }),
    capture_ocr_images: Object.assign(new HTMLSelectElement(), { value: '' }),
    include_ocr_blocks: Object.assign(new HTMLInputElement(), { type: 'checkbox', checked: false }),
    extract_ocr_header: Object.assign(new HTMLInputElement(), { type: 'checkbox', checked: false }),
    extract_ocr_footer: Object.assign(new HTMLInputElement(), { type: 'checkbox', checked: false }),
    ocr_table_format: Object.assign(new HTMLSelectElement(), { value: '' }),
    ocr_confidence: Object.assign(new HTMLSelectElement(), { value: '' }),
};
roundTripForm.elements = { namedItem(name) { return controls[name] || null; } };
VaultExplorerImportOptions.preserve(roundTripForm, {
    clean_html: false,
    strategies: ['custom_strategy'],
    pdf_strategies: ['pdf_ocr'],
    include_ocr_blocks: false,
    extract_ocr_header: true,
});
assert.deepStrictEqual(VaultExplorerImportOptions.read(roundTripForm), {
    clean_html: false,
    strategies: ['custom_strategy'],
    pdf_strategies: ['pdf_ocr'],
    include_ocr_blocks: false,
    extract_ocr_header: true,
    queue_only: false,
});
controls.pdf_strategy.value = 'ocr';
roundTripForm.dataset.importTouchedOptions = '["pdf_strategy"]';
assert.deepStrictEqual(VaultExplorerImportOptions.read(roundTripForm), {
    clean_html: false,
    pdf_strategies: ['pdf_ocr'],
    include_ocr_blocks: false,
    extract_ocr_header: true,
    queue_only: false,
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_IMPORT_OPTIONS_MODULE), str(_IMPORTS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_vault_explorer_import_forms_use_compact_progressive_layout() -> None:
    source = _IMPORTS_MODULE.read_text(encoding="utf-8")
    options_source = _IMPORT_OPTIONS_MODULE.read_text(encoding="utf-8")

    assert "vault-explorer-import-primary" in source
    assert "vault-explorer-import-source-count" in source
    assert "sources.map" not in source
    assert "vault-explorer-import-footer" in source
    assert "vault-explorer-import-options-grid" in options_source
    assert "data-vault-explorer-import-change-destination" in source
    assert "Reset to current defaults" in options_source
    assert "Overrides active" in options_source
    assert "options.onGetImportJob" in source
    assert "Import is still processing" in source
    assert "VaultExplorerImportOptions" in source
    assert '<details class="vault-explorer-import-advanced">' in options_source
    assert "vault-explorer-action-header" not in source
