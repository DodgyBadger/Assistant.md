"""Frontend smoke tests for the file reference module graph."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LINKS_MODULE = _PROJECT_ROOT / "static/js/file-reference-links.js"
_REFERENCES_MODULE = _PROJECT_ROOT / "static/js/file-references.js"


def test_file_references_composes_link_resolver() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
let importRequest = null;
global.fetch = async (url, options) => {
    importRequest = { url, options };
    return { ok: true, json: async () => ({ job_id: 17 }) };
};
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

let pickerOptions = null;
const controller = FileReferences.create({
    state: { workspaceExists: true },
    elements: {
        vaultSelector: { value: 'ChatVault' },
        workspacePathInput: { value: 'Projects' },
    },
    icons: {},
    utils: {},
    callbacks: {
        openPathPicker(options) { pickerOptions = options; },
        setWorkspace() { return true; },
    },
});

for (const name of [
    'openPicker',
    'openExplorer',
    'openFile',
    'enhanceFileLinks',
    'syncInteractionLocks',
]) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing file reference method: ${name}`);
    }
}

controller.openExplorer({ vaultName: 'ArchiveVault' });
assert.strictEqual(pickerOptions.workspacePath, '');
assert.strictEqual(pickerOptions.onAddReference, undefined);
assert.strictEqual(pickerOptions.onSetWorkspace, undefined);

controller.openExplorer({
    vaultName: 'ArchiveVault',
    importSources: ['Incoming/report.pdf'],
});
assert.deepStrictEqual(pickerOptions.importSources, ['Incoming/report.pdf']);
assert.strictEqual(typeof pickerOptions.onImportSources, 'function');
pickerOptions.onImportSources({ sources: ['Incoming/report.pdf'] }).then((result) => {
    assert.deepStrictEqual(result, { job_id: 17 });
    assert.strictEqual(importRequest.url, 'api/import/sources');
    assert.deepStrictEqual(JSON.parse(importRequest.options.body), {
        sources: ['Incoming/report.pdf'],
        vault: 'ArchiveVault',
    });
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});

controller.openExplorer();
assert.strictEqual(pickerOptions.workspacePath, 'Projects');
assert.strictEqual(typeof pickerOptions.onAddReference, 'function');
assert.strictEqual(typeof pickerOptions.onSetWorkspace, 'function');
pickerOptions.onSetWorkspace('New Workspace').then((saved) => {
    assert.strictEqual(saved, true);
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_LINKS_MODULE), str(_REFERENCES_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_file_reference_links_load_before_controller() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    links_position = markup.index(
        '<script src="static/js/file-reference-links.js"></script>'
    )
    references_position = markup.index(
        '<script src="static/js/file-references.js"></script>'
    )

    assert links_position < references_position


def test_adjacent_at_path_references_do_not_merge_across_prose() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
const source = fs.readFileSync(process.argv[1], 'utf8').replace(
    'return Object.freeze({ enhanceFileLinks });',
    'global.__candidateMatches = candidateMatches; return Object.freeze({ enhanceFileLinks });'
);
vm.runInThisContext(source, { filename: process.argv[1] });
FileReferenceLinks.create({
    callbacks: {
        selectedVault() { return 'PHHC'; },
        workspacePath() { return ''; },
        openDirectory() {},
        openFile() {},
    },
});

const text = 'The working package and full preview are at @PHHC/Cana reports/2026-10/ and @PHHC/Cana reports/2026-10/Board summary and publication preview.md.';
assert.deepStrictEqual(
    global.__candidateMatches(text).map(({ raw, candidate }) => ({ raw, candidate })),
    [
        {
            raw: '@PHHC/Cana reports/2026-10/',
            candidate: 'PHHC/Cana reports/2026-10',
        },
        {
            raw: '@PHHC/Cana reports/2026-10/Board summary and publication preview.md',
            candidate: 'PHHC/Cana reports/2026-10/Board summary and publication preview.md',
        },
    ]
);
"""
    subprocess.run(
        ["node", "-e", harness, str(_LINKS_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
