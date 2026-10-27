"""Framework self-check using an independent file-producing application."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def check(artifacts_dir: Path) -> None:
    for mode, primary, phase in (
        ("success", None, None),
        ("setup_failure", "ValueError", "setup"),
        ("assertion_failure", "AssertionError", "execution"),
        ("cleanup_failure", "AssertionError", "execution"),
        ("timeout", "TimeoutError", "execution"),
        ("cancel", "CancelledError", "execution"),
        ("evidence_failure", None, None),
        ("close_failure", None, None),
        ("application_timeout", "TimeoutError", "execution"),
        ("cleanup_timeout", None, None),
        ("cleanup_only_failure", None, None),
        ("cleanup_interrupt", None, None),
        ("parallel_events", None, None),
        ("unprintable_failure", "UnprintableError", "setup"),
    ):
        root = artifacts_dir / mode
        root.mkdir()
        completed = subprocess.run(
            [
                sys.executable,
                "-S",
                str(
                    Path(__file__).resolve().parent
                    / "fixtures"
                    / "lifecycle_application.py"
                ),
                str(root),
                mode,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert completed.returncode == 0, completed.stderr
        report = json.loads((root / "fixture-result.json").read_text())
        terminal = json.loads((root / "artifacts/lifecycle-result.json").read_text())
        assert terminal["passed"] is report["passed"]
        assert report["primary_type"] == primary, report
        assert report["primary_phase"] == phase, report
        assert report["same_loop"] is True
        assert report["close_attempts"] == (2 if mode == "evidence_failure" else 1)
        assert report["application_imported"] is False
        assert report["passed"] is (mode in {"success", "parallel_events"})
        resources = (root / "resource.log").read_text().splitlines()
        assert (
            resources.count("opened")
            == resources.count("closed")
            == resources.count("stopped")
            == 1
        )
        assert resources.index("closed") < resources.index("stopped")
        if mode in {"success", "parallel_events"}:
            assert (root / "output.md").read_text() == "# Actual fixture output\n"
            events = [
                json.loads(line)
                for line in (root / "artifacts/events.jsonl").read_text().splitlines()
            ]
            assert any(event["event"] == "application_stopped" for event in events)
            assert events[-1]["event"] == "lifecycle_finished"
        if mode == "parallel_events":
            assert (
                len(
                    [
                        event
                        for event in events
                        if event["event"] == "concurrent_fixture"
                    ]
                )
                == 100
            )
        if mode == "cleanup_failure":
            assert [error["name"] for error in report["cleanup_errors"]] == [
                "failed-resource",
                "adapter.teardown",
            ]
        if mode == "evidence_failure":
            assert report["evidence_errors"]
        assert report["cancelled"] is (mode in {"cancel", "cleanup_interrupt"})
        assert report["timed_out"] is (mode in {"timeout", "cleanup_timeout"})
        for artifact in (
            root / "fixture-result.json",
            root / "artifacts/events.jsonl",
        ):
            assert "fixture-private-credential" not in artifact.read_text()
