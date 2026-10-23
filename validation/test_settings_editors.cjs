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

test('restoring a default updates provenance and preserves another unsaved draft', async () => {
    const { context, actions, state } = harness();
    const result = { key: 'limit', value: '3', default_value: '3', source: 'default', editor: numeric, issues: [] };
    state.settings = [{ ...result, value: '9', source: 'custom' }];
    state.settingEditKey = 'other';
    state.settingDraftValue = 'unsaved';
    const calls = [];
    context.fetch = async (url, options = {}) => {
        calls.push({ url, method: options.method || 'GET' });
        return { ok: true, json: async () => options.method === 'DELETE' ? result : [result] };
    };
    await actions.restoreSettingDefault('limit');
    assert.equal(calls.filter((call) => call.method === 'DELETE').length, 1);
    assert.equal(state.settings[0].source, 'default');
    assert.equal(state.settingEditKey, 'other');
    assert.equal(state.settingDraftValue, 'unsaved');
    assert.equal(state.isSavingSetting, false);
});

test('global defaults cancellation leaves drafts and configuration untouched', async () => {
    const { context, actions, state } = harness();
    state.settingEditKey = 'limit';
    state.settingDraftValue = '9';
    context.confirm = () => false;
    context.fetch = async () => { throw new Error('Cancellation must not send a request'); };
    await actions.restoreManagedDefaults();
    assert.equal(state.settingEditKey, 'limit');
    assert.equal(state.settingDraftValue, '9');
    assert.equal(state.isSavingSetting, false);
});

test('failed global reset keeps the unsaved draft and exposes the conflict', async () => {
    const { context, actions, state, elements } = harness();
    state.settingEditKey = 'limit';
    state.settingDraftValue = '9';
    context.confirm = () => true;
    context.fetch = async () => ({ ok: false, status: 400, json: async () => ({ message: 'Custom model conflicts with a managed default' }) });
    await actions.restoreManagedDefaults();
    assert.equal(state.settingEditKey, 'limit');
    assert.equal(state.settingDraftValue, '9');
    assert.match(elements.settingsFeedback.textContent, /conflict/);
    assert.equal(state.isSavingSetting, false);
});

test('custom setting reset renders the shared Undo icon instead of the fallback gear', () => {
    const { context, actions, helpers } = harness();
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/icons.js'), 'utf8'), context);
    const icons = context.AssistantMDIcons;
    assert.equal(helpers.iconSvg('undo'), icons.UNDO_ICON_SVG);
    const card = actions.renderSettingViewCard({ key: 'limit', value: '9', default_value: '3', source: 'custom', editor: numeric, issues: [] });
    assert.ok(card.includes(icons.UNDO_ICON_SVG));
    assert.ok(!card.includes(icons.SETTINGS_ICON_SVG));
});

test('saved settings unlock and repaint while metadata is still refreshing', async () => {
    const { context, actions, callbacks, state, elements } = harness();
    const result = { key: 'limit', value: '7', default_value: '3', source: 'custom', editor: numeric, issues: [] };
    state.settings = [result];
    state.settingEditKey = 'limit';
    state.settingDraftValue = '7';
    let releaseMetadata;
    let metadataStarted = false;
    callbacks.refreshMetadata = () => { metadataStarted = true; return new Promise(resolve => { releaseMetadata = resolve; }); };
    context.fetch = async (_url, options = {}) => ({ ok: true, json: async () => options.method === 'PUT' ? result : [result] });
    const saving = actions.saveSettingValue('limit');
    while (!metadataStarted) await new Promise(resolve => setImmediate(resolve));
    assert.equal(state.isSavingSetting, false);
    assert.doesNotMatch(elements.settingsList.innerHTML, /data-action="restore-setting-default" disabled/);
    releaseMetadata();
    await saving;
});

test('default restore unlocks before slow status refresh finishes', async () => {
    const { context, actions, callbacks, state, elements } = harness();
    const result = { key: 'limit', value: '3', default_value: '3', source: 'default', editor: numeric, issues: [] };
    const other = { ...result, key: 'other', source: 'custom', value: '9' };
    state.settings = [{ ...result, source: 'custom' }, other];
    let releaseStatus;
    let statusStarted = false;
    callbacks.refreshStatus = () => { statusStarted = true; return new Promise(resolve => { releaseStatus = resolve; }); };
    context.fetch = async (_url, options = {}) => ({ ok: true, json: async () => options.method === 'DELETE' ? result : [result, other] });
    const restoring = actions.restoreSettingDefault('limit');
    while (!statusStarted) await new Promise(resolve => setImmediate(resolve));
    assert.equal(state.isSavingSetting, false);
    assert.doesNotMatch(elements.settingsList.innerHTML, /data-action="restore-setting-default" disabled/);
    releaseStatus();
    await restoring;
});

test('finishing an older metadata refresh cannot unlock a newer pending mutation', async () => {
    const { context, actions, callbacks, state } = harness();
    const first = { key: 'limit', value: '7', default_value: '3', source: 'custom', editor: numeric, issues: [] };
    const other = { ...first, key: 'other', value: '9' };
    state.settings = [first, other];
    state.settingEditKey = 'limit';
    state.settingDraftValue = '7';
    let releaseMetadata, releaseReset;
    let metadataStarted = false;
    callbacks.refreshMetadata = async () => {
        if (!metadataStarted) {
            metadataStarted = true;
            await new Promise(resolve => { releaseMetadata = resolve; });
        }
    };
    context.fetch = async (_url, options = {}) => {
        if (options.method === 'DELETE') {
            await new Promise(resolve => { releaseReset = resolve; });
            return { ok: true, json: async () => ({ ...other, value: '3', source: 'default' }) };
        }
        return { ok: true, json: async () => options.method === 'PUT' ? first : [first, other] };
    };
    const saving = actions.saveSettingValue('limit');
    while (!metadataStarted) await new Promise(resolve => setImmediate(resolve));
    const restoring = actions.restoreSettingDefault('other');
    while (!releaseReset) await new Promise(resolve => setImmediate(resolve));
    releaseMetadata();
    await saving;
    assert.equal(state.isSavingSetting, true);
    releaseReset();
    await restoring;
    assert.equal(state.isSavingSetting, false);
});

test('successive settings changes coalesce slow refreshes and retain save feedback', async () => {
    const { context, actions, callbacks, state, elements } = harness();
    let entries = ['first', 'second', 'third'].map(key => ({ key, value: '9', default_value: '3', source: 'custom', editor: numeric, issues: [] }));
    state.settings = entries;
    let releaseMetadata;
    let refreshes = 0;
    callbacks.refreshMetadata = async () => {
        refreshes += 1;
        if (refreshes === 1) await new Promise(resolve => { releaseMetadata = resolve; });
    };
    context.fetch = async (url, options = {}) => {
        if (options.method === 'DELETE') {
            const key = url.split('/').at(-2);
            const result = { ...entries.find(entry => entry.key === key), value: '3', source: 'default' };
            entries = entries.map(entry => entry.key === key ? result : entry);
            return { ok: true, json: async () => result };
        }
        return { ok: true, json: async () => entries };
    };
    const first = actions.restoreSettingDefault('first');
    while (!releaseMetadata) await new Promise(resolve => setImmediate(resolve));
    const second = actions.restoreSettingDefault('second');
    while (state.isSavingSetting) await new Promise(resolve => setImmediate(resolve));
    const third = actions.restoreSettingDefault('third');
    while (state.isSavingSetting) await new Promise(resolve => setImmediate(resolve));
    assert.equal(refreshes, 1);
    assert.match(elements.settingsFeedback.textContent, /now uses its default/);
    releaseMetadata();
    await Promise.all([first, second, third]);
    assert.equal(refreshes, 2);
    assert.ok(state.settings.every(entry => entry.source === 'default'));
    assert.match(elements.settingsFeedback.textContent, /now uses its default/);
});

test('global defaults refresh visible values even while an older metadata refresh waits', async () => {
    const { context, actions, callbacks, state } = harness();
    let entries = ['first', 'other'].map(key => ({ key, value: '9', default_value: '3', source: 'custom', editor: numeric, issues: [] }));
    state.settings = entries;
    let releaseMetadata;
    let calls = 0;
    callbacks.refreshMetadata = async () => {
        calls += 1;
        if (calls === 1) await new Promise(resolve => { releaseMetadata = resolve; });
    };
    context.confirm = () => true;
    context.fetch = async (_url, options = {}) => {
        if (options.method === 'POST') {
            entries = entries.map(entry => ({ ...entry, value: '3', source: 'default' }));
            return { ok: true, json: async () => ({ message: 'Defaults restored' }) };
        }
        if (options.method === 'DELETE') {
            const result = { ...entries[0], value: '3', source: 'default' };
            entries = [result, entries[1]];
            return { ok: true, json: async () => result };
        }
        return { ok: true, json: async () => entries };
    };
    const first = actions.restoreSettingDefault('first');
    while (!releaseMetadata) await new Promise(resolve => setImmediate(resolve));
    const reset = actions.restoreManagedDefaults();
    while (state.isSavingSetting) await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(state.settings.every(entry => entry.source === 'default'));
    releaseMetadata();
    await Promise.all([first, reset]);
});
