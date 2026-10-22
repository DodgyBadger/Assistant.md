const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function harness() {
    const context = vm.createContext({
        document: {},
        requestAnimationFrame: callback => callback(),
        console,
    });
    context.window = context;
    vm.runInContext(fs.readFileSync("static/js/icons.js", "utf8"), context);
    for (const file of ['runtime', 'models']) {
        vm.runInContext(fs.readFileSync(`static/js/configuration/${file}.js`, 'utf8'), context);
    }
    const runtime = context.ConfigurationPanelRuntime;
    runtime.elements.modelList = { innerHTML: '', querySelector() { return null; } };
    runtime.state.providers = [{ name: 'openai' }];
    runtime.state.models = [{
        name: 'managed', provider: 'openai', model_string: 'provider-model',
        managed_id: 'stable', user_editable: false, capabilities: ['text', 'vision'],
        alternate_aliases: ['old'], available: true,
    }];
    return { context, ...runtime };
}

test('managed editing retains editable alternate names and protects binding controls', () => {
    const runtime = harness();
    runtime.actions.startModelEdit('managed');
    assert.equal(runtime.state.modelDraft.binding_read_only, true);
    const html = runtime.elements.modelList.innerHTML;
    assert.match(html, /data-field="name" readonly/);
    assert.match(html, /data-field="model_string" disabled/);
    assert.match(html, /data-field="alternate_aliases" class=/);
    assert.equal(runtime.state.modelDraft.alternate_aliases, 'old');
});

test('duplicate drafts are independent custom models with no inherited aliases', () => {
    const runtime = harness();
    runtime.actions.duplicateModel('managed');
    const draft = runtime.state.modelDraft;
    assert.equal(draft.name, 'managed-custom');
    assert.equal(draft.model_string, 'provider-model');
    assert.equal(draft.binding_read_only, false);
    assert.equal(draft.alternate_aliases, '');
    assert.equal(draft.managed_id, undefined);
    assert.equal(runtime.state.modelEdit.mode, 'new');
});

test('custom rename sends one atomic request and preserves a failed draft', async () => {
    const runtime = harness();
    runtime.state.models[0].managed_id = null;
    runtime.state.models[0].user_editable = true;
    runtime.actions.startModelEdit('managed');
    runtime.state.modelDraft.name = 'renamed';
    const requests = [];
    runtime.context.fetch = async (url, options) => {
        requests.push({ url, options });
        return { ok: false, status: 400, json: async () => ({ message: 'Collision' }) };
    };
    await runtime.actions.saveModelRow('managed');
    assert.equal(requests.length, 1);
    assert.equal(requests[0].options.method, 'PUT');
    const payload = JSON.parse(requests[0].options.body);
    assert.equal(payload.original_name, 'managed');
    assert.deepEqual(payload.alternate_aliases, ['old']);
    assert.equal(runtime.state.modelDraft.name, 'renamed');
    assert.equal(runtime.state.modelEdit.key, 'managed');
});

test('pending saves keep the draft protected until failure is reported', async () => {
    const runtime = harness();
    runtime.actions.startModelEdit('managed');
    let finish;
    runtime.context.fetch = () => new Promise(resolve => { finish = resolve; });
    const saving = runtime.actions.saveModelRow('managed');
    assert.equal(runtime.state.isSavingModel, true);
    runtime.actions.cancelModelEdit();
    assert.equal(runtime.state.modelEdit?.key, 'managed');
    runtime.actions.handleModelInputChange({ target: { dataset: { field: 'alternate_aliases' }, value: 'changed-during-save' } });
    assert.equal(runtime.state.modelDraft.alternate_aliases, 'old');
    finish({ ok: false, status: 400, json: async () => ({ message: 'Collision' }) });
    await saving;
    assert.equal(runtime.state.isSavingModel, false);
    assert.equal(runtime.state.modelEdit.key, 'managed');
});
