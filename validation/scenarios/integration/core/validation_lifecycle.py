"""Framework self-check using an independent file-producing application."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from validation.core.base_scenario import BaseScenario


class ValidationLifecycleScenario(BaseScenario):
    async def test_scenario(self) -> None:
        app_root = Path(__file__).resolve().parents[4]
        for mode, primary, phase in (
            ("success", None, None),
            ("setup_failure", "ValueError", "setup"),
            ("assertion_failure", "AssertionError", "execution"),
            ("cleanup_failure", "AssertionError", "execution"),
            ("timeout", "TimeoutError", "execution"),
            ("cancel", "CancelledError", "execution"),
            ("evidence_failure", None, None),
        ):
            root = self.artifacts_dir / mode
            root.mkdir()
            environment = dict(os.environ, PYTHONPATH=str(app_root))
            completed = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    "-m",
                    "validation.fixtures.lifecycle_application",
                    str(root),
                    mode,
                ],
                cwd=app_root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert completed.returncode == 0, completed.stderr
            report = json.loads((root / "fixture-result.json").read_text())
            assert report["primary_type"] == primary, report
            assert report["primary_phase"] == phase, report
            assert report["same_loop"] is True
            assert report["application_imported"] is False
            assert report["passed"] is (mode == "success")
            resources = (root / "resource.log").read_text().splitlines()
            assert (
                resources.count("opened")
                == resources.count("closed")
                == resources.count("stopped")
                == 1
            )
            assert resources.index("closed") < resources.index("stopped")
            if mode == "success":
                assert (root / "output.md").read_text() == "# Actual fixture output\n"
                events = [
                    json.loads(line)
                    for line in (root / "artifacts/events.jsonl")
                    .read_text()
                    .splitlines()
                ]
                assert any(event["event"] == "application_stopped" for event in events)
                assert events[-1]["event"] == "run_completed"
            if mode == "cleanup_failure":
                assert [error["name"] for error in report["cleanup_errors"]] == [
                    "failed-resource",
                    "adapter.teardown",
                ]
            if mode == "evidence_failure":
                assert report["evidence_errors"]
            assert report["cancelled"] is (mode == "cancel")
            assert report["timed_out"] is (mode == "timeout")
            for artifact in (
                root / "fixture-result.json",
                root / "artifacts/events.jsonl",
            ):
                assert "fixture-private-credential" not in artifact.read_text()
        self.teardown_scenario()
