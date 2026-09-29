"""Frontend smoke tests for read-only stepped session-map inspection."""

from __future__ import annotations

import subprocess
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _PROJECT_ROOT / "static/js/session-map.js"


def test_session_map_controller_contract() -> None:
    harness = r"""
const fs = require('fs');
const vm = require('vm');

global.window = global;
global.document = { getElementById: () => null };
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), { filename: process.argv[1] });

const controller = SessionMap.create({
    elements: {},
    icons: {},
    utils: { escapeHtml: value => String(value) },
});

for (const name of ['closeModal', 'openModalForSession']) {
    if (typeof controller[name] !== 'function') {
        throw new Error(`Missing session-map method: ${name}`);
    }
}
"""
    subprocess.run(
        ["node", "-e", harness, str(_MODULE)],
        check=True,
        cwd=_PROJECT_ROOT,
    )


def test_session_map_loads_before_application() -> None:
    markup = (_PROJECT_ROOT / "static/index.html").read_text(encoding="utf-8")

    map_position = markup.index('<script src="static/js/session-map.js"></script>')
    application_position = markup.index('<script src="static/app.js"></script>')

    assert map_position < application_position


def test_session_map_renders_narrative_trajectory() -> None:
    source = _MODULE.read_text(encoding="utf-8")

    assert "How We Got Here" in source
    assert "renderSources(trajectory)" in source


def test_session_map_exposes_checkpoint_and_transcript_actions() -> None:
    map_source = _MODULE.read_text(encoding="utf-8")
    history_source = (_PROJECT_ROOT / "static/js/chat-history-rendering.js").read_text(
        encoding="utf-8"
    )

    assert "renderTranscript(payload.transcript" in map_source
    assert "data-session-map-transcript-page" in map_source
    assert "data-session-map-tool-call" in map_source
    assert "callbacks.openToolCall" in map_source
    assert (
        '<details class="session-map-transcript-message session-map-transcript-tools">'
        in map_source
    )
    assert "session-map-checkpoint-link" in history_source
    assert "message.context_checkpoint_kind === 'session_map'" in history_source
    assert "callbacks.openSessionMap" in history_source
