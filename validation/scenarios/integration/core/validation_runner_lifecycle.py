"""Framework self-check through real CLI execution and actual application resources."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from validation.core.base_scenario import BaseScenario

_MODE_ENV = "ASSISTANTMD_VALIDATION_RUNNER_FIXTURE_MODE"
_SELECTOR = "integration/core/validation_runner_lifecycle"


class ValidationRunnerLifecycleScenario(BaseScenario):
    def __init__(self) -> None:
        super().__init__()
        if os.environ.get(_MODE_ENV) == "constructor_failure":
            self.create_vault("ConstructorFailureVault")
            self._get_system_controller()
            raise ValueError("fixture constructor failed after resource acquisition")

    async def test_scenario(self) -> None:
        mode = os.environ.get(_MODE_ENV)
        if mode:
            self.create_vault("RunnerLifecycleVault")
            await self.start_system()
            assert self.call_api("/api/status").status_code == 200
            if mode == "cancel":
                raise asyncio.CancelledError("fixture execution interrupted")
            if mode == "timeout":
                await asyncio.Event().wait()
            if mode == "combined_failure":
                self.timeline_file.unlink()
                self.timeline_file.mkdir()
                raise AssertionError("fixture primary contract failed")
            if mode == "cleanup_only_failure":
                self.timeline_file.unlink()
                self.timeline_file.mkdir()
            return

        checkout = Path(__file__).resolve().parents[4]
        for mode in (
            "success",
            "constructor_failure",
            "combined_failure",
            "cleanup_only_failure",
            "cancel",
            "timeout",
            "source_mismatch",
        ):
            root = self.artifacts_dir / mode
            root.mkdir()
            if mode == "source_mismatch":
                candidate = root / "scenarios" / f"{_SELECTOR}.py"
                candidate.parent.mkdir(parents=True)
                candidate.write_text(
                    "from validation.core.base_scenario import BaseScenario\n"
                    "class ValidationRunnerLifecycleScenario(BaseScenario):\n"
                    "    async def test_scenario(self):\n"
                    "        raise AssertionError('selected fixture source')\n"
                )
            else:
                (root / "scenarios").symlink_to(
                    checkout / "validation/scenarios", target_is_directory=True
                )
            command = [
                sys.executable,
                str(checkout / "validation/run_validation.py"),
                "run",
                _SELECTOR,
            ]
            if mode == "cancel":
                command.append("integration/core/validation_inventory")
            if mode == "timeout":
                command.extend(["--timeout", "0.02"])
            environment = dict(
                os.environ,
                **{
                    _MODE_ENV: mode,
                    "VALIDATION_ROOT": str(root),
                    "CONTAINER_DATA_ROOT": str(root / "unused-data"),
                    "CONTAINER_SYSTEM_ROOT": str(root / "unused-system"),
                },
            )
            completed = subprocess.run(
                command,
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                timeout=90,
            )
            (root / "process.log").write_text(completed.stdout + completed.stderr)
            assert completed.returncode == (
                0 if mode == "success" else 130 if mode == "cancel" else 1
            ), (completed.stdout + completed.stderr)
            report = json.loads((root / "runs/reports/latest.json").read_text())
            result = report["scenario_results"][0]
            assert result["scenario_name"] == _SELECTOR
            assert (result["status"] == "passed") is (mode == "success")
            assert (
                report["total_scenarios"]
                == report["passed_scenarios"]
                + report["failed_scenarios"]
                + report["error_scenarios"]
            )
            evidence = Path(result["evidence_path"])
            lifecycle = json.loads(
                (evidence / "artifacts/lifecycle-result.json").read_text()
            )
            assert lifecycle["passed"] is (mode == "success")
            if mode == "combined_failure":
                assert result["status"] == "failed"
                assert "fixture primary contract failed" in result["error_message"]
                assert {error["phase"] for error in result["lifecycle_errors"]} == {
                    "cleanup",
                    "evidence",
                }
            if mode == "source_mismatch":
                assert result["status"] == "framework_error"
            if mode == "cleanup_only_failure":
                assert result["status"] == "framework_error"
                assert result["lifecycle_errors"]
            if mode == "timeout":
                assert result["timed_out"] and not result["interrupted"]
            if mode == "cancel":
                assert report["interrupted"] and report["total_scenarios"] == 2
                assert report["scenario_results"][1]["execution_phase"] == "not_started"
            events = [
                json.loads(line)
                for line in (evidence / "artifacts/events.jsonl")
                .read_text()
                .splitlines()
            ]
            if mode not in {"timeout", "source_mismatch"} or any(
                event.get("name") == "assistantmd.controller" for event in events
            ):
                assert any(
                    event["event"] == "cleanup_callback_completed"
                    and event.get("name") == "assistantmd.controller"
                    for event in events
                )
        self.teardown_scenario()
