"""Framework self-check through real CLI execution and actual application resources."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from scenario_harness.lifecycle import current_run_context

from validation.core.base_scenario import BaseScenario
from validation.core.results import ScenarioResult

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
            if mode == "isolation":
                assert "ASSISTANTMD_WORKER_MUTATION" not in os.environ
                assert "ASSISTANTMD_AMBIENT_SECRET" not in os.environ
                assert Path.home().is_relative_to(self.run_path)
                assert Path(tempfile.gettempdir()).is_relative_to(self.run_path)
                os.environ["ASSISTANTMD_WORKER_MUTATION"] = "changed"
                (self.artifacts_dir / "worker-state.json").write_text(
                    json.dumps({"pid": os.getpid()})
                )
                return
            if mode == "secret_environment":
                context = current_run_context()
                assert context is not None
                secret = context.secret("ASSISTANTMD_EXPLICIT_SECRET")
                assert os.environ["ASSISTANTMD_EXPLICIT_SECRET"] == secret
                assert "ASSISTANTMD_AMBIENT_SECRET" not in os.environ
                context.emit("fixture_secret_input", credential=secret)
                print(secret)
                print(secret, file=sys.stderr)
                encoded = secret.encode()
                os.write(1, encoded[:8])
                os.write(1, encoded[8:] + b"\n")
                sys.stderr.buffer.write(encoded + b"\n")
                sys.stderr.buffer.flush()
                return
            if mode == "crash":
                os._exit(7)
            if mode == "supervisor_evidence_failure":
                (self.artifacts_dir / "supervisor-result.json").mkdir()
                return
            if mode == "forged_success":
                result = ScenarioResult(
                    _SELECTOR, "passed", 0, evidence_path=str(self.run_path)
                )
                (self.run_path / "worker-result.json").write_text(
                    json.dumps({"schema_version": 1, "result": result.to_dict()})
                )
                os._exit(0)
            if mode == "hard_block":
                (self.artifacts_dir / "blocking-body-started").touch()
                time.sleep(60)
                return
            if mode == "hard_cleanup":
                context = current_run_context()
                assert context is not None
                context.defer_cleanup(
                    "fixture.blocking_cleanup", lambda: time.sleep(60)
                )
                return
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
            "isolation",
            "secret_environment",
            "crash",
            "forged_success",
            "hard_block",
            "hard_cleanup",
            "supervisor_evidence_failure",
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
            if mode == "isolation":
                command.append(_SELECTOR)
            command.extend(["--env", _MODE_ENV])
            if mode == "secret_environment":
                command.extend(["--secret-env", "ASSISTANTMD_EXPLICIT_SECRET"])
            if mode in {"hard_block", "hard_cleanup"}:
                command.extend(["--timeout", "20", "--cleanup-timeout", "0.2"])
            if mode == "timeout":
                command.extend(["--timeout", "0.02"])
            environment = dict(
                os.environ,
                **{
                    _MODE_ENV: mode,
                    "VALIDATION_ROOT": str(root),
                    "CONTAINER_DATA_ROOT": str(root / "unused-data"),
                    "CONTAINER_SYSTEM_ROOT": str(root / "unused-system"),
                    "ASSISTANTMD_AMBIENT_SECRET": "fixture-ambient-not-inherited",
                    "ASSISTANTMD_EXPLICIT_SECRET": "fixture-explicit-sensitive-input",
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
                0
                if mode in {"success", "isolation", "secret_environment"}
                else 130 if mode == "cancel" else 1
            ), (completed.stdout + completed.stderr)
            report = json.loads((root / "runs/reports/latest.json").read_text())
            result = report["scenario_results"][0]
            assert result["scenario_name"] == _SELECTOR
            assert (result["status"] == "passed") is (
                mode in {"success", "isolation", "secret_environment"}
            )
            assert (
                report["total_scenarios"]
                == report["passed_scenarios"]
                + report["failed_scenarios"]
                + report["error_scenarios"]
            )
            evidence = Path(result["evidence_path"])
            if mode == "supervisor_evidence_failure":
                assert result["status"] == "framework_error"
                assert result["execution_phase"] == "evidence"
                assert any(
                    error["name"] == "supervisor_result"
                    for error in result["lifecycle_errors"]
                )
                continue
            supervisor = json.loads(
                (evidence / "artifacts/supervisor-result.json").read_text()
            )
            assert supervisor["result"] == result
            if mode in {"crash", "forged_success", "hard_block", "hard_cleanup"}:
                assert (
                    result["status"] == "framework_error"
                    and result["execution_phase"] == "worker"
                )
                assert result["timed_out"] is (mode in {"hard_block", "hard_cleanup"})
                if mode == "hard_block":
                    assert (evidence / "artifacts/blocking-body-started").exists()
                if mode == "hard_cleanup":
                    records = (evidence / "artifacts/events.jsonl").read_text()
                    assert "fixture.blocking_cleanup" in records
                assert not (evidence / "artifacts/lifecycle-result.json").exists()
                continue
            if mode == "isolation":
                states = [
                    json.loads(
                        (
                            Path(row["evidence_path"]) / "artifacts/worker-state.json"
                        ).read_text()
                    )
                    for row in report["scenario_results"]
                ]
                assert len(states) == 2 and states[0]["pid"] != states[1]["pid"]
            if mode == "secret_environment":
                for path in evidence.rglob("*"):
                    if path.is_file():
                        assert (
                            b"fixture-explicit-sensitive-input" not in path.read_bytes()
                        ), path
                assert (
                    "[REDACTED]"
                    in (evidence / "artifacts/process/stdout.txt").read_text()
                )
                assert (
                    "[REDACTED]"
                    in (evidence / "artifacts/process/stderr.txt").read_text()
                )
            lifecycle = json.loads(
                (evidence / "artifacts/lifecycle-result.json").read_text()
            )
            assert lifecycle["passed"] is (
                mode in {"success", "isolation", "secret_environment"}
            )
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
            if mode not in {
                "timeout",
                "source_mismatch",
                "isolation",
                "secret_environment",
            } or any(event.get("name") == "assistantmd.controller" for event in events):
                assert any(
                    event["event"] == "cleanup_callback_completed"
                    and event.get("name") == "assistantmd.controller"
                    for event in events
                )
        self.teardown_scenario()
