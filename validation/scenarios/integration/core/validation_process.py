"""Exercise the generic supervisor with independent OS processes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from validation.core.base_scenario import BaseScenario


def _running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # An orphaned zombie is already dead, although its adopter may not reap it.
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            return stat.read_text().split(") ", 1)[1][0] != "Z"
        except FileNotFoundError:
            return False
    return True


class ValidationProcessScenario(BaseScenario):
    async def test_scenario(self) -> None:
        if os.name != "posix":
            self.teardown_scenario()
            return
        app_root = Path(__file__).resolve().parents[4]
        for mode in (
            "success",
            "redact",
            "nonzero",
            "block",
            "ignore_term",
            "descendant_normal",
            "descendant_timeout",
            "interrupt",
        ):
            root = self.artifacts_dir / mode
            root.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-m",
                    "validation.fixtures.process_application",
                    str(root),
                    mode,
                ],
                cwd=app_root,
                env=dict(os.environ, PYTHONPATH=str(app_root)),
                capture_output=True,
                text=True,
                timeout=15,
            )
            assert completed.returncode == 0, (mode, completed.stderr)
            result = json.loads((root / "result.json").read_text())
            assert result["timed_out"] is (
                mode in {"block", "ignore_term", "descendant_timeout"}
            ), (mode, result)
            assert result["interrupted"] is (mode == "interrupt"), (mode, result)
            assert result["elapsed"] < 5, (mode, result)
            if mode in {"success", "nonzero"}:
                assert result["returncode"] == (0 if mode == "success" else 7)
                assert (root / "artifacts/stdout.txt").read_text() == "fixture stdout\n"
                assert (root / "artifacts/stderr.txt").read_text() == "fixture stderr\n"
            if mode == "redact":
                expected = b"split [REDACTED] end\ndescendant [REDACTED] end\n"
                assert (root / "artifacts/stdout.txt").read_bytes() == expected
                assert (root / "artifacts/stderr.txt").read_bytes() == expected
            if mode.startswith("descendant_") or mode == "interrupt":
                pid = int((root / "child.pid").read_text())
                deadline = time.monotonic() + 2
                while _running(pid) and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert not _running(pid), (mode, pid)
        self.teardown_scenario()
