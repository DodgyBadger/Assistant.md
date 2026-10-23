"""Frontend smoke tests for chat selection coordination."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/chat-selection.js"


def test_chat_selection_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = ChatSelection.create({
    state: {},
    elements: {},
    sessionControls: {},
    callbacks: {},
});

for (const name of [
    'fetchMetadata',
    'populateSelectors',
    'resetChatModeToDefault',
    'isChatSelectableModel',
    'persistSelectedChatMode',
    'handleVaultChange',
    'fetchTemplates',
]) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing chat selection method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_chat_selection_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    selection_position = markup.index(
        '<script src="static/js/chat-selection.js"></script>'
    )
    application_position = markup.index('<script src="static/app.js"></script>')

    assert selection_position < application_position


def test_chat_model_selection_resolves_alternates_and_uses_only_the_default() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
global.window = global;
global.document = { createElement() { return {}; } };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
function select(value = '') {
    return { value, options: [], innerHTML: '', appendChild(option) { this.options.push(option); } };
}
const models = [
    { name: 'first', available: true, chat_selectable: true },
    { name: 'preferred', available: true, chat_selectable: true, alternate_aliases: ['old'] },
];
const state = { metadata: { vaults: [], models, settings: {} }, systemStatus: { configuration_status: { default_model: 'old' } } };
const elements = { vaultSelector: select(), modelSelector: select(), thinkingSelector: null };
const controller = ChatSelection.create({ state, elements, sessionControls: { renderSelector() {} }, callbacks: { syncControls() {} } });
controller.populateSelectors();
assert.equal(elements.modelSelector.value, 'preferred');
assert.equal(new Set(elements.modelSelector.options.map(option => option.textContent)).size, 2);
assert.equal(elements.modelSelector.options.filter(item => item.value === 'old').length, 0);
elements.modelSelector.value = 'old';
controller.populateSelectors();
assert.equal(elements.modelSelector.value, 'preferred');
models[1].available = false;
controller.populateSelectors();
assert.equal(elements.modelSelector.value, 'preferred');
elements.modelSelector.value = 'removed';
state.systemStatus.configuration_status.default_model = 'missing';
controller.populateSelectors();
assert.equal(elements.modelSelector.value, '');
models.push(
    {name: 'invalid-a', chat_selectable: false, available: false, alternate_aliases: ['ambiguous']},
    {name: 'invalid-b', chat_selectable: false, available: false, alternate_aliases: ['ambiguous']},
);
elements.modelSelector.value = 'ambiguous';
state.systemStatus.configuration_status.default_model = 'old';
controller.populateSelectors();
assert.equal(elements.modelSelector.value, 'preferred');
elements.modelSelector.value = 'invalid-a';
state.systemStatus.configuration_status.default_model = 'ambiguous';
controller.populateSelectors();
assert.equal(elements.modelSelector.value, '');
"""
    subprocess.run(["node", "-e", harness, str(_MODULE)], check=True, cwd=_PROJECT_ROOT)
