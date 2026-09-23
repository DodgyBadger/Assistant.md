"""Frontend smoke tests for the file reference module graph."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_LINKS_MODULE = _PROJECT_ROOT / "static/js/file-reference-links.js"
_REFERENCES_MODULE = _PROJECT_ROOT / "static/js/file-references.js"


def test_file_references_composes_link_resolver() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

const controller = FileReferences.create({
    state: {},
    elements: {},
    icons: {},
    utils: {},
    callbacks: {},
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
