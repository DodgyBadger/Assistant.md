"""Settings input fixtures and observations for real application scenarios."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from validation.scenarios.application import Application


def general_settings(app: Application) -> dict[str, Any]:
    response = app.request("/api/system/settings/general")
    assert response.status_code == 200, response.text
    return {entry["key"]: entry for entry in response.json()}


def activity_events(app: Application) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (app.root / "system" / "activity.log").read_text().splitlines()
    ]


def run_settings_process(app: Application, root: Path, source: str) -> None:
    """Use public bootstrap roots in a fresh interpreter, without strategy patches."""
    script = (
        "from pathlib import Path\n"
        "from core.runtime.paths import set_bootstrap_roots\n"
        f"set_bootstrap_roots(Path({str(app.root / 'process-bootstrap-data')!r}), Path({str(app.root / 'process-bootstrap-system')!r}))\n"
        "import core.logger\n"
        f"set_bootstrap_roots(Path({str(root / 'data')!r}), Path({str(root)!r}))\n"
        + source
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    app.evidence.append(
        "settings-processes.jsonl",
        {
            "root": str(root),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )
    assert result.returncode == 0, result.stderr
