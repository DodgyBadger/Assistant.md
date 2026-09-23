"""Frontend smoke tests for application shell coordination."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/app-shell.js"


def test_app_shell_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = { getElementById() { return null; } };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = AppShell.create({
    browserStorage: { getItem() { return 'light'; }, setItem() {} },
    callbacks: {},
});

for (const name of ['init', 'switchTab']) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing application shell method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_app_shell_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    shell_position = markup.index('<script src="static/js/app-shell.js"></script>')
    application_position = markup.index('<script src="static/app.js"></script>')

    assert shell_position < application_position
