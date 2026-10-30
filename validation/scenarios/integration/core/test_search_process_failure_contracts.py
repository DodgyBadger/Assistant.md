"""Actual process-environment failures retain stable search outcomes and cleanup."""

import json
import sys

import pytest

from validation.scenarios.support.settings import run_settings_process

pytestmark = pytest.mark.scenario


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("missing", "ripgrep_not_found"),
        ("unusable", "search_failed"),
        ("timeout", "timeout"),
    ],
)
async def test_search_external_execution_failures_are_distinct_and_bounded(
    app, mode, expected
):
    vault = app.vault("SearchFailureVault")
    app.write(vault, "Evidence.md", "needle")
    await app.start()
    tools = app.root / "external-search-path"
    tools.mkdir()
    pid = app.root / "external-search.pid"
    if mode != "missing":
        program = tools / "rg"
        # This is an external executable/environment substitute, not an app
        # service mock. Real rg behavior is covered elsewhere. Here the actual
        # OS start/deadline/termination paths must produce the search outcome.
        program.write_text(
            f"#!{sys.executable}\nimport os,time\nfrom pathlib import Path\nPath({str(pid)!r}).write_text(str(os.getpid()))\ntime.sleep(20)\n"
        )
        program.chmod(0o700 if mode == "timeout" else 0o600)
    output = app.root / "search-process-result.json"
    run_settings_process(
        app,
        app.root / "isolated-search-state",
        f"""
import json,os
from pathlib import Path
from core.vault_state.search import search_vault_content, VaultContentSearchError
os.environ["PATH"] = {str(tools)!r}
try:
    search_vault_content(vault_path=Path({str(vault)!r}), query="needle", timeout_seconds=0.5)
except VaultContentSearchError as error:
    result = {{"code": error.code, "message": str(error)}}
else:
    raise AssertionError("The real external execution must fail")
pid_path=Path({str(pid)!r})
if pid_path.exists():
    try: os.kill(int(pid_path.read_text()),0)
    except ProcessLookupError: result["child_exited"] = True
    else: result["child_exited"] = False
Path({str(output)!r}).write_text(json.dumps(result))
""",
    )
    result = json.loads(output.read_text())
    assert result["code"] == expected
    if mode == "timeout":
        assert pid.exists() and result["child_exited"]
    app.evidence.save_json("external-search-outcome.json", result)
