"""Opt-in check of the actual desktop development launcher."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx


def exercise(output: Path) -> int:
    checks: dict[str, bool] = {}
    report: dict[str, object] = {"checks": checks}
    try:
        with TemporaryDirectory(prefix="assistantmd-browser-launcher-") as temporary:
            root = Path(temporary)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            environment = dict(
                os.environ, ASSISTANTMD_DEV_RUNTIME_ROOT=str(root / "runtime")
            )
            missing = subprocess.run(
                [
                    "scripts/dev",
                    "run",
                    "--browser-handoff",
                    "--port",
                    str(port),
                ],
                env=dict(
                    environment,
                    PLAYWRIGHT_BROWSERS_PATH=str(root / "empty-browser-cache"),
                ),
                capture_output=True,
                text=True,
                timeout=20,
            )
            diagnostics = missing.stdout + missing.stderr
            assert missing.returncode != 0, "missing_browser_rejected"
            checks["missing_browser_rejected"] = True
            assert (
                "uv run python -m playwright install chromium" in diagnostics
            ), "missing_browser_reports_install_command"
            checks["missing_browser_reports_install_command"] = True
            assert "Traceback" not in diagnostics, "missing_browser_has_no_traceback"
            checks["missing_browser_has_no_traceback"] = True
            with (root / "launcher.log").open("w") as log:
                process = subprocess.Popen(
                    [
                        "scripts/dev",
                        "run",
                        "--browser-handoff",
                        "--port",
                        str(port),
                    ],
                    env=environment,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
                try:
                    deadline = time.monotonic() + 30
                    with httpx.Client(
                        base_url=f"http://127.0.0.1:{port}", timeout=2
                    ) as client:
                        while True:
                            if process.poll() is not None:
                                raise AssertionError("launcher_exited_before_ready")
                            try:
                                if client.get("/").status_code == 200:
                                    break
                            except httpx.TransportError:
                                pass
                            if time.monotonic() > deadline:
                                raise AssertionError("launcher_startup_timeout")
                            time.sleep(0.1)
                        checks["special_flag_starts_prototype"] = True
                        response = client.get("/human/state")
                        assert (
                            response.status_code == 200
                            and response.json()["owner"] == "agent"
                        ), "passwordless_local_access"
                        checks["passwordless_local_access"] = True
                        assert not (
                            root / "runtime"
                        ).exists(), "main_runtime_state_not_initialized"
                        checks["main_runtime_state_not_initialized"] = True
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                    # Reap any stray process in this exercise's own launch group.
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        checks["launcher_children_cleaned_up"] = True
                    else:
                        processes = subprocess.run(
                            ["ps", "-eo", "pgid=,stat=,comm="],
                            capture_output=True,
                            text=True,
                            check=True,
                        )
                        report["remaining_processes"] = [
                            line.split()[1:]
                            for line in processes.stdout.splitlines()
                            if line.split() and line.split()[0] == str(process.pid)
                        ]
                        os.killpg(process.pid, signal.SIGKILL)
                        checks["launcher_children_cleaned_up"] = False
            assert checks.get(
                "launcher_children_cleaned_up"
            ), "launcher_children_cleaned_up"
        report["verdict"] = "passed"
    except Exception as exc:  # Safe experiment boundary; capabilities stay private.
        report["verdict"] = "failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, AssertionError):
            report["failed_check"] = str(exc)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    return exercise(parser.parse_args().output)


if __name__ == "__main__":
    raise SystemExit(main())
