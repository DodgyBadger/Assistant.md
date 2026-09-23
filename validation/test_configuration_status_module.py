"""Frontend smoke tests for configuration status coordination."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/configuration-status.js"


def test_configuration_status_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = { getElementById() { return null; } };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = ConfigurationStatus.create({
    state: {},
    elements: {},
    browserStorage: {
        getItem() { return null; },
        setItem() {},
        removeItem() {},
    },
    icons: {},
    utils: {},
    callbacks: {},
});

for (const name of ['update', 'setRestartRequired', 'syncRestartFlagWithStorage']) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing configuration status method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_configuration_status_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    status_position = markup.index(
        '<script src="static/js/configuration-status.js"></script>'
    )
    application_position = markup.index('<script src="static/app.js"></script>')

    assert status_position < application_position
