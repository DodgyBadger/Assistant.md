"""Frontend smoke tests for system and task status coordination."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/system-status.js"


def test_system_status_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = SystemStatus.create({
    state: {},
    chatElements: {},
    dashElements: {},
    configElements: { advancedShellCoordinates: [] },
    dashboardView: {},
    callbacks: {},
});

for (const name of ['fetchSystemStatus', 'fetchExecutionTasks', 'display']) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing system status method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_system_status_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    status_position = markup.index('<script src="static/js/system-status.js"></script>')
    application_position = markup.index('<script src="static/app.js"></script>')

    assert status_position < application_position
