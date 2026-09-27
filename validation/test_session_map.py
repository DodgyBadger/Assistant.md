"""Frontend smoke tests for read-only stepped session-map inspection."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/session-map.js"


def test_session_map_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = { getElementById: () => null };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = SessionMap.create({
    elements: {},
    icons: {},
    utils: { escapeHtml: value => String(value) },
});

for (const name of ['closeModal', 'openModalForSession']) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing session-map method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_session_map_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    map_position = markup.index('<script src="static/js/session-map.js"></script>')
    application_position = markup.index('<script src="static/app.js"></script>')

    assert map_position < application_position
