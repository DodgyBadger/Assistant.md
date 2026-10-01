"""Frontend behavior tests for dashboard execution-task discovery."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/dashboard-view.js"


def test_dashboard_polls_for_tasks_while_visible() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.AssistantMDIcons = {};
let intervalCallback = null;
let clearedTimer = null;
global.setInterval = (callback, delay) => {
    assert.strictEqual(delay, 2000);
    intervalCallback = callback;
    return 17;
};
global.clearInterval = (timer) => { clearedTimer = timer; };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const fetches = [];
const state = {
    executionTasks: [],
    executionTaskPollTimer: null,
    dashboardActive: false,
};
const controller = DashboardView.create({
    state,
    elements: {},
    utils: { escapeHtml(value) { return String(value); }, formatShortDate() { return ''; } },
    callbacks: {
        fetchExecutionTasks(options) { fetches.push(options); },
        isTerminalTaskStatus() { return false; },
    },
});

controller.syncExecutionTaskPolling();
assert.strictEqual(state.executionTaskPollTimer, null);

state.dashboardActive = true;
controller.syncExecutionTaskPolling();
assert.strictEqual(state.executionTaskPollTimer, 17);
assert.strictEqual(typeof intervalCallback, 'function');
intervalCallback();
assert.deepStrictEqual(fetches, [{ render: true }]);

state.dashboardActive = false;
controller.syncExecutionTaskPolling();
assert.strictEqual(clearedTimer, 17);
assert.strictEqual(state.executionTaskPollTimer, null);
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )
