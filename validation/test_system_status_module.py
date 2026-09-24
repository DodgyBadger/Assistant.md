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


def test_initial_render_does_not_wait_for_system_status() -> None:
    source = (_PROJECT_ROOT / "static/app.js").read_text(encoding="utf-8")
    init_source = source[
        source.index("async function init()") : source.index("// Setup tab switching")
    ]

    assert "await fetchMetadata();" in init_source
    assert "await fetchSystemStatus();" not in init_source
    assert init_source.index("renderChatEmptyState();") < init_source.index(
        "void fetchSystemStatus();"
    )


def test_late_system_status_preserves_user_model_selection() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const state = {
    metadata: {
        models: [
            { name: 'first', available: true },
            { name: 'preferred', available: true },
        ],
    },
    modelSelectionTouched: true,
};
const modelSelector = { value: 'first' };
global.fetch = async (url) => ({
    ok: true,
    async json() {
        if (url === 'api/status') {
            return {
                advanced_shell: {},
                configuration_status: { default_model: 'preferred' },
            };
        }
        return { tasks: [] };
    },
});
const controller = SystemStatus.create({
    state,
    chatElements: { modelSelector },
    dashElements: {},
    configElements: { advancedShellCoordinates: [] },
    dashboardView: {
        displaySystemStatus() {},
        syncExecutionTaskPolling() {},
    },
    callbacks: {
        isChatSelectableModel() { return true; },
        syncRestartFlag() {},
        updateStatus() {},
        refreshChatEmptyState() {},
    },
});

(async () => {
    await controller.fetchSystemStatus();
    assert.strictEqual(modelSelector.value, 'first');
    state.modelSelectionTouched = false;
    await controller.fetchSystemStatus();
    assert.strictEqual(modelSelector.value, 'preferred');
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
