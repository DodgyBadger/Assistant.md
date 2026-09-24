"""Smoke tests for the configuration panel's browser module graph."""

from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_STATIC_ROOT = _PROJECT_ROOT / "static"
_EXPECTED_SCRIPTS = [
    "static/js/configuration/runtime.js",
    "static/js/configuration/activity-log.js",
    "static/js/configuration/settings.js",
    "static/js/configuration/models.js",
    "static/js/configuration/openai-oauth.js",
    "static/js/configuration/providers.js",
    "static/js/configuration/connections.js",
    "static/js/configuration/mcp-connections.js",
    "static/js/configuration/secrets.js",
    "static/js/configuration/maintenance.js",
    "static/js/configuration/imports.js",
    "static/js/configuration/import-jobs.js",
    "static/js/configuration.js",
]


class _ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "script":
            return
        source = dict(attrs).get("src")
        if source is not None:
            self.sources.append(source)


def test_configuration_modules_load_in_declared_order() -> None:
    parser = _ScriptParser()
    parser.feed((_STATIC_ROOT / "index.html").read_text(encoding="utf-8"))
    configuration_sources = [
        source
        for source in parser.sources
        if source == "static/js/configuration.js"
        or source.startswith("static/js/configuration/")
    ]

    assert configuration_sources == _EXPECTED_SCRIPTS

    script_paths = [str(_PROJECT_ROOT / source) for source in configuration_sources]
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {
    getElementById() { return null; },
    addEventListener(_name, handler) {
        if (typeof handler !== 'function') throw new Error('Invalid document listener');
    },
};
global.addEventListener = (_name, handler) => {
    if (typeof handler !== 'function') throw new Error('Invalid window listener');
};

for (const path of JSON.parse(process.argv[1])) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

ConfigurationPanel.init({});
for (const name of [
    'init',
    'onTabActivated',
    'onTabDeactivated',
    'onDashboardActivated',
    'refreshActivityLog',
    'onMetadataUpdated',
    'setRestartRequired',
]) {
    if (typeof ConfigurationPanel[name] !== 'function') {
        throw new Error(`Missing ConfigurationPanel.${name}`);
    }
}
for (const name of [
    'loadModels',
    'renderModels',
    'handleModelTableClick',
    'loadProviders',
    'renderProviders',
    'handleProviderTableClick',
    'renderOpenAiOAuthPanel',
    'startOpenAiOAuth',
    'loadMcpConnections',
    'renderMcpConnections',
    'handleMcpConnectionAction',
    'loadImportJobs',
    'renderImportJobs',
    'handleImportJobAction',
]) {
    if (typeof ConfigurationPanelRuntime.actions[name] !== 'function') {
        throw new Error(`Missing configuration action: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, json.dumps(script_paths)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_dashboard_import_owns_defaults_and_job_observability_only() -> None:
    markup = (_STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    imports_source = (_STATIC_ROOT / "js/configuration/imports.js").read_text(
        encoding="utf-8"
    )
    jobs_source = (_STATIC_ROOT / "js/configuration/import-jobs.js").read_text(
        encoding="utf-8"
    )

    assert 'id="import-defaults-save"' in markup
    assert 'id="import-open-explorer"' in markup
    assert 'id="import-jobs-list"' in markup
    assert 'id="import-scan"' not in markup
    assert 'id="import-url-form"' not in markup
    assert 'id="import-url-input"' not in markup
    assert "renderImportOutputLinks" in imports_source
    assert "data-import-source-path" in imports_source
    assert "job.can_resubmit" in jobs_source
    assert "importOptions: job.request_options" in jobs_source
    assert "request.importSources" in jobs_source


def test_openai_provider_renders_with_shared_configuration_actions() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {};
global.requestAnimationFrame = (callback) => callback();
for (const path of process.argv.slice(1)) {
    vm.runInThisContext(fs.readFileSync(path, 'utf8'), { filename: path });
}

const runtime = ConfigurationPanelRuntime;
runtime.elements.providerList = {
    innerHTML: '',
    querySelector() { return null; },
};
runtime.elements.modelList = {
    innerHTML: '',
    querySelector() { return null; },
};
runtime.state.providers = [{
    name: 'openai',
    user_editable: false,
    oauth_enabled: false,
    oauth_status: 'disabled',
    configured_auth_mode: 'api_key',
    effective_auth_mode: 'api_key',
}];
runtime.state.models = [];
runtime.state.modelsLoadFailed = true;

runtime.actions.renderProviders();
runtime.actions.renderModels();
assert.match(runtime.elements.providerList.innerHTML, /OpenAI OAuth/);
assert.match(runtime.elements.modelList.innerHTML, /Unable to load model mappings/);
"""
    subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(_STATIC_ROOT / "js/configuration/runtime.js"),
            str(_STATIC_ROOT / "js/configuration/openai-oauth.js"),
            str(_STATIC_ROOT / "js/configuration/models.js"),
            str(_STATIC_ROOT / "js/configuration/providers.js"),
        ],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_system_sections_start_independently() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = {
    getElementById() { return null; },
    addEventListener() {},
};
global.addEventListener = () => {};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
    filename: process.argv[1],
});

const started = [];
const reportedErrors = [];
let finishActivityLog;
const loaderNames = [
    'refreshActivityLog',
    'loadProviders',
    'loadGeneralSettings',
    'loadModels',
    'loadSecrets',
    'loadGoogleConnection',
    'loadMcpConnections',
    'loadSystemJobs',
    'loadSystemMigrations',
    'loadImportVaults',
    'loadPurgeSessionsVaults',
    'loadCleanupGoalsVaults',
];
for (const name of loaderNames) {
    ConfigurationPanelRuntime.actions[name] = () => {
        started.push(name);
        if (name === 'refreshActivityLog') {
            return new Promise((resolve) => { finishActivityLog = resolve; });
        }
        if (name === 'loadProviders') throw new Error('provider render failed');
        if (name === 'loadModels') return Promise.reject(new Error('model request failed'));
        return Promise.resolve();
    };
}
ConfigurationPanelRuntime.actions.cancelAllOAuthPolls = () => {};
console.error = (...args) => reportedErrors.push(args);

vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'), {
    filename: process.argv[2],
});
ConfigurationPanel.init({});

(async () => {
    const refresh = ConfigurationPanel.onTabActivated();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepStrictEqual(started, loaderNames);
    finishActivityLog();
    await refresh;
    assert.strictEqual(ConfigurationPanelRuntime.state.hasLoadedOnce, true);
    assert.strictEqual(reportedErrors.length, 2);
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""
    subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(_STATIC_ROOT / "js/configuration/runtime.js"),
            str(_STATIC_ROOT / "js/configuration.js"),
        ],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_dashboard_sections_start_independently() -> None:
    harness = r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = { getElementById() { return null; }, addEventListener() {} };
global.addEventListener = () => {};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const started = [];
const reportedErrors = [];
let finishSecrets;
ConfigurationPanelRuntime.actions.loadSecrets = () => {
    started.push('loadSecrets');
    return new Promise((resolve) => { finishSecrets = resolve; });
};
ConfigurationPanelRuntime.actions.loadImportVaults = () => {
    started.push('loadImportVaults');
    throw new Error('vault render failed');
};
ConfigurationPanelRuntime.actions.loadImportJobs = () => {
    started.push('loadImportJobs');
    return Promise.resolve();
};
ConfigurationPanelRuntime.actions.cancelAllOAuthPolls = () => {};
console.error = (...args) => reportedErrors.push(args);

vm.runInThisContext(fs.readFileSync(process.argv[2], 'utf8'), { filename: process.argv[2] });
ConfigurationPanel.init({});

(async () => {
    const refresh = ConfigurationPanel.onDashboardActivated();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepStrictEqual(started, ['loadSecrets', 'loadImportVaults', 'loadImportJobs']);
    finishSecrets();
    await refresh;
    assert.strictEqual(reportedErrors.length, 1);
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
"""
    subprocess.run(
        [
            "node",
            "-e",
            harness,
            str(_STATIC_ROOT / "js/configuration/runtime.js"),
            str(_STATIC_ROOT / "js/configuration.js"),
        ],
        check=True,
        cwd=_PROJECT_ROOT,
    )
