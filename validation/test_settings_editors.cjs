const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

function harness() {
    const context = vm.createContext({
        document: {}, console,
        requestAnimationFrame: (fn) => fn(),
        AssistantMDIcons: { iconSvg: () => '', },
    });
    context.window = context;
    for (const name of ['runtime', 'settings']) {
        vm.runInContext(fs.readFileSync(path.join(__dirname, `../static/js/configuration/${name}.js`), 'utf8'), context);
    }
    const runtime = context.ConfigurationPanelRuntime;
    const element = () => ({ innerHTML: '', textContent: '', hidden: false, querySelector: () => null, classList: { add() {}, remove() {} } });
    runtime.elements.settingsList = element();
    runtime.elements.settingsValidation = element();
    runtime.elements.settingsFeedback = element();
    runtime.actions.updateImportOcrAvailability = () => {};
    return { context, ...runtime };
}

const numeric = { kind: 'integer', minimum: 0, maximum: 32, exclusive_minimum: false };
const choice = { kind: 'select', choices: [{ value: 'current', label: 'Current' }] };
const list = { kind: 'ordered_list', choices: [{ value: 'ocr', label: 'OCR' }, { value: 'text', label: 'Text' }], min_items: 1, unique_items: true };

test('draft validation rejects invalid numbers, choices, and list entries', () => {
    const { actions } = harness();
    for (const raw of ['', 'NaN', 'Infinity', '-1', '33', '1.5']) assert.ok(actions.validateSettingDraft({ editor: numeric }, raw), raw);
    assert.equal(actions.validateSettingDraft({ editor: numeric }, '0'), '');
    assert.ok(actions.validateSettingDraft({ editor: choice }, 'removed'));
    assert.equal(actions.validateSettingDraft({ editor: choice }, 'current'), '');
    for (const raw of ['[]', '[42]', '["ocr","ocr"]', '["missing"]']) assert.ok(actions.validateSettingDraft({ editor: list }, raw));
    assert.equal(actions.validateSettingDraft({ editor: list }, '["text","ocr"]'), '');
});

test('warnings survive filtering and transient status feedback until every issue clears', () => {
    const { actions, state, elements, helpers } = harness();
    state.settings = [
        { key: 'default_model', value: '<removed>', editor: choice, issues: [{ code: 'choice', message: '<removed> is unavailable' }] },
        { key: 'limit', value: '-1', editor: numeric, issues: [{ code: 'range', message: 'Too small' }] },
    ];
    state.settingsFilter = 'no matches';
    actions.renderSettings();
    assert.equal(elements.settingsValidation.hidden, false);
    assert.match(elements.settingsValidation.innerHTML, /default_model/);
    assert.match(elements.settingsValidation.innerHTML, /limit/);
    assert.doesNotMatch(elements.settingsValidation.innerHTML, /<removed>/);
    helpers.setStatus(elements.settingsFeedback, 'Saved', 'success');
    assert.equal(elements.settingsValidation.hidden, false);
    state.settings[0].issues = [];
    actions.renderSettings();
    assert.equal(elements.settingsValidation.hidden, false);
    state.settings[1].issues = [];
    actions.renderSettings();
    assert.equal(elements.settingsValidation.hidden, true);
});

test('unavailable choice is selected without silently choosing a replacement', () => {
    const { actions } = harness();
    const output = actions.renderSettingControl({ key: 'default_model', editor: choice }, 'removed');
    assert.match(output, /value="removed" selected disabled/);
    assert.doesNotMatch(output, /value="current" selected/);
});

test('ordered list actions preserve explicit order and serialize changes', () => {
    const { actions, state } = harness();
    state.settings = [{ key: 'strategies', value: '["ocr","text"]', editor: list }];
    state.settingEditKey = 'strategies';
    state.settingDraftValue = '["ocr","text"]';
    const card = { dataset: { setting: 'strategies' }, querySelector: () => ({ value: 'ocr' }) };
    actions.handleSettingListAction('list-up', { dataset: { index: '1' } }, card);
    assert.deepEqual(JSON.parse(state.settingDraftValue), ['text', 'ocr']);
    actions.handleSettingListAction('list-remove', { dataset: { index: '1' } }, card);
    assert.deepEqual(JSON.parse(state.settingDraftValue), ['text']);
    actions.handleSettingListAction('list-add', { dataset: {} }, card);
    assert.deepEqual(JSON.parse(state.settingDraftValue), ['text', 'ocr']);
    actions.handleSettingsInputChange({ target: { dataset: { field: 'setting-list-item', index: '0' }, value: 'changed' } });
    assert.deepEqual(JSON.parse(state.settingDraftValue), ['changed', 'ocr']);
});

test('failed save refreshes choices and warnings while preserving draft', async () => {
    const { context, actions, state, elements } = harness();
    state.settings = [{ key: 'model', value: 'current', editor: choice, issues: [] }];
    state.settingEditKey = 'model';
    state.settingDraftValue = 'current';
    const requests = [];
    context.fetch = async (_url, options) => {
        requests.push(options);
        if (options?.method === 'PUT') return { ok: false, status: 400, json: async () => ({ message: 'Alias was removed' }) };
        return { ok: true, json: async () => [{ key: 'model', value: 'current', editor: { ...choice, choices: [] }, issues: [{ code: 'choice', message: 'Missing alias' }] }] };
    };
    await actions.saveSettingValue('model');
    assert.equal(state.settingDraftValue, 'current');
    assert.equal(state.settingEditKey, 'model');
    assert.equal(elements.settingsValidation.hidden, false);
    assert.match(elements.settingsFeedback.textContent, /Alias was removed/);
    assert.deepEqual(JSON.parse(requests[0].body), { value: 'current' });
});

test('failed refresh retains saved warnings and reports validation unavailable', async () => {
    const { context, actions, state, elements } = harness();
    state.settings = [{ key: 'model', value: 'missing', issues: [{ code: 'choice', message: 'Missing' }] }];
    context.fetch = async () => { throw new Error('offline'); };
    await actions.loadGeneralSettings(true);
    assert.match(elements.settingsValidation.innerHTML, /unavailable/);
    assert.match(elements.settingsValidation.innerHTML, /model/);
    assert.equal(elements.settingsValidation.hidden, false);
});

test('late refresh responses cannot overwrite newer choices or warnings', async () => {
    const { context, actions, state } = harness();
    const pending = [];
    context.fetch = () => new Promise((resolve) => pending.push(resolve));
    const first = actions.loadGeneralSettings(true);
    const second = actions.loadGeneralSettings(true);
    pending[1]({ ok: true, json: async () => [{ key: 'new', value: 'latest', issues: [] }] });
    await second;
    pending[0]({ ok: true, json: async () => [{ key: 'old', value: 'stale', issues: [] }] });
    await first;
    assert.equal(state.settings[0].key, 'new');
    assert.equal(state.isLoadingSettings, false);
});

test('cancel during an editor refresh does not reopen the cancelled editor', async () => {
    const { context, actions, state } = harness();
    let finish;
    context.fetch = () => new Promise((resolve) => { finish = resolve; });
    const opening = actions.startSettingEdit('model');
    actions.cancelSettingEdit();
    finish({ ok: true, json: async () => [{ key: 'model', value: 'current', editor: choice, issues: [] }] });
    await opening;
    assert.equal(state.settingEditKey, null);
    assert.equal(state.settingDraftValue, '');
});


test('editing an invalid numeric value keeps the saved value visible and intact', () => {
    const { actions, state } = harness();
    state.settingDraftValue = 'broken-number';
    const setting = { key: 'limit', value: 'broken-number', editor: numeric, issues: [{ code: 'type', message: 'Invalid integer' }] };
    const output = actions.renderSettingEditCard(setting);
    const visibleText = output.replace(/<[^>]*>/g, '');
    assert.match(visibleText, /broken-number/);
    assert.equal(state.settingDraftValue, 'broken-number');
    assert.equal(setting.value, 'broken-number');
});
