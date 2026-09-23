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
    "static/js/configuration/providers.js",
    "static/js/configuration/connections.js",
    "static/js/configuration/mcp-connections.js",
    "static/js/configuration/secrets.js",
    "static/js/configuration/maintenance.js",
    "static/js/configuration/imports.js",
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
        if source.startswith("static/js/configuration")
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
    'loadMcpConnections',
    'renderMcpConnections',
    'handleMcpConnectionAction',
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
