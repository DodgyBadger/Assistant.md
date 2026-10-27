"""Framework self-check against real AssistantMD startup and partial failures."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from validation.core.base_scenario import BaseScenario


class ValidationAdapterLifecycleScenario(BaseScenario):
    async def test_scenario(self) -> None:
        checkout = Path(__file__).resolve().parents[4]
        for mode in (
            "success",
            "prepared_failure",
            "prepare_failure",
            "startup_failure",
            "execution_failure",
            "startup_cancel",
            "invalid_configuration",
            "restart",
            "thread_logging",
            "cache_restore",
            "adapter_setup_failure",
        ):
            root = self.artifacts_dir / mode
            root.mkdir()
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "validation.fixtures.assistantmd_lifecycle_application",
                    str(root),
                    mode,
                ],
                cwd=checkout,
                env=dict(os.environ, PYTHONPATH=str(checkout)),
                capture_output=True,
                text=True,
                timeout=60,
            )
            (root / "process.log").write_text(process.stdout + process.stderr)
            assert process.returncode == 0, process.stderr
            result = json.loads((root / "adapter-result.json").read_text())
            assert result["passed"] is (
                mode in {"success", "restart", "thread_logging", "cache_restore"}
            ), result
            assert result["cancelled"] is (mode == "startup_cancel"), result
            assert result["startup_cancel_injected"] is (
                mode == "startup_cancel"
            ), result
            assert result["cleanup_errors"] == result["evidence_errors"] == 0, result
            for key in (
                "roots_restored",
                "environment_restored",
                "runtime_released",
                "background_tasks_released",
                "clock_restored",
                "client_closed",
                "borrowed_handler_preserved",
                "caller_streams_preserved",
                "owned_streams_closed",
                "model_cache_restored",
            ):
                assert result[key], (mode, key, result)
            events = [
                json.loads(line)
                for line in (root / "artifacts/events.jsonl").read_text().splitlines()
            ]
            if mode == "adapter_setup_failure":
                assert result["primary_phase"] == "setup"
                continue
            captured = [
                entry["record"]
                for entry in events
                if entry["event"] == "application_event"
            ]
            before = next(
                record
                for record in captured
                if record.get("data", {}).get("event") == "fixture_before_start"
            )
            assert before["data"]["credential"] == "[REDACTED]"
            if mode == "cache_restore":
                assert result["caller_stream_count"] > 0
            if mode == "invalid_configuration":
                assert result["primary_type"] == "RuntimeConfigError"
            activity = [
                json.loads(line)
                for line in (root / "bootstrap/system/activity.log")
                .read_text()
                .splitlines()
            ]
            before_activity = next(
                entry
                for entry in activity
                if entry.get("data", {}).get("event") == "fixture_before_start"
            )
            assert before_activity["data"]["credential"] == "[REDACTED]"
            if mode == "thread_logging":
                threaded = next(
                    record
                    for record in captured
                    if record.get("data", {}).get("event") == "fixture_plain_thread"
                )
                assert threaded["data"]["credential"] == "[REDACTED]"
                assert (
                    "fixture-private-api-credential"
                    not in (root / "alternate-system/activity.log").read_text()
                )
            if mode in {"success", "restart", "thread_logging", "cache_restore"}:
                interactions = (root / "artifacts/system_interactions.log").read_text()
                assert "FIXTURE_API_CREDENTIAL" in interactions
                assert "[REDACTED]" in interactions
                assert "fixture-private-api-credential" not in interactions
                assert (
                    root / "application-output.md"
                ).read_text() == "# Real application completed\n"
            if mode in {"success", "execution_failure"}:
                assert any(
                    entry["event"] == "cleanup_callback_completed"
                    and entry["name"] == "assistantmd.controller"
                    for entry in events
                )
        self.teardown_scenario()
