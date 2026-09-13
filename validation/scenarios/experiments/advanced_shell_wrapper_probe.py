"""Probe forced-command environment and remote process-group cleanup locally."""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WRAPPER_PATH = REPOSITORY_ROOT / "docker/advanced-shell/forced_command.py"


def _load_wrapper() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "assistantmd_advanced_shell_entry", WRAPPER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the advanced-shell forced command.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wrapper_process(workspace: Path, command: str) -> subprocess.Popen[str]:
    launcher = (
        "import importlib.util, pathlib; "
        f"spec=importlib.util.spec_from_file_location('entry', {str(WRAPPER_PATH)!r}); "
        "module=importlib.util.module_from_spec(spec); "
        "spec.loader.exec_module(module); "
        f"module.WORKSPACE_ROOT=pathlib.Path({str(workspace)!r}); "
        f"module.ALLOWED_WORKING_ROOTS=(pathlib.Path({str(workspace)!r}),); "
        "raise SystemExit(module.main())"
    )
    environment = dict(os.environ)
    environment["SSH_ORIGINAL_COMMAND"] = command
    environment["ASSISTANTMD_PROBE_SENTINEL"] = "must-not-cross"
    return subprocess.Popen(
        [sys.executable, "-c", launcher],
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _structured_command(
    *,
    executable: str,
    args: list[str],
    cwd: Path,
    env: dict[str, str] | None = None,
) -> str:
    payload = json.dumps(
        {
            "executable": executable,
            "args": args,
            "cwd": str(cwd),
            "env": env or {},
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode("ascii")
    return f"assistantmd-stdio-v1:{encoded}"


def _wait_for_file(path: Path, timeout_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {path.name}")


def _assert_pid_gone(pid: int, message: str) -> None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    raise AssertionError(message)


def main() -> None:
    """Exercise deterministic behavior without requiring Docker or sshd."""
    module = _load_wrapper()
    environment = module._execution_environment()
    assert "ASSISTANTMD_PROBE_SENTINEL" not in environment
    assert set(environment) == {
        "HOME",
        "LANG",
        "LC_ALL",
        "NPM_CONFIG_PREFIX",
        "PATH",
        "SHELL",
        "TMPDIR",
        "UV_TOOL_BIN_DIR",
        "UV_TOOL_DIR",
    }
    assert environment["PATH"].startswith("/home/advanced-shell/.local/bin:")
    assert environment["NPM_CONFIG_PREFIX"] == "/home/advanced-shell/.local"

    with tempfile.TemporaryDirectory(prefix="advanced-shell-wrapper-") as root:
        workspace = Path(root)
        basic = _wrapper_process(
            workspace,
            "pwd; printf 'stdout-value'; printf 'stderr-value' >&2; "
            'test -z "${ASSISTANTMD_PROBE_SENTINEL:-}"; exit 7',
        )
        basic.wait(timeout=5)
        assert basic.stdout is not None
        assert basic.stderr is not None
        stdout = basic.stdout.read()
        stderr = basic.stderr.read()
        assert basic.stdin is not None
        basic.stdin.close()
        assert basic.returncode == 7
        assert stdout == f"{workspace}\nstdout-value"
        assert stderr == "stderr-value"

        injection_marker = workspace / "must-not-exist"
        literal_argument = f"$(touch {injection_marker})"
        structured = _wrapper_process(
            workspace,
            _structured_command(
                executable=sys.executable,
                args=[
                    "-c",
                    "import os,sys; print(sys.argv[1]); print(os.environ['PROBE_MODE'])",
                    literal_argument,
                ],
                cwd=workspace,
                env={"PROBE_MODE": "structured"},
            ),
        )
        structured.wait(timeout=5)
        assert structured.stdout is not None
        assert structured.stderr is not None
        structured_stdout = structured.stdout.read()
        structured_stderr = structured.stderr.read()
        assert structured.stdin is not None
        structured.stdin.close()
        assert structured.returncode == 0, structured_stderr
        assert structured_stdout == f"{literal_argument}\nstructured\n"
        assert structured_stderr == ""
        assert not injection_marker.exists()

        invalid_structured = _wrapper_process(
            workspace,
            _structured_command(
                executable="python",
                args=[],
                cwd=workspace,
            ),
        )
        _stdout, invalid_stderr = invalid_structured.communicate(timeout=5)
        assert invalid_structured.returncode == 64
        assert "absolute path" in invalid_stderr

        traversing_structured = _wrapper_process(
            workspace,
            _structured_command(
                executable=sys.executable,
                args=[],
                cwd=workspace / "child" / "..",
            ),
        )
        _stdout, traversing_stderr = traversing_structured.communicate(timeout=5)
        assert traversing_structured.returncode == 64
        assert "outside allowed roots" in traversing_stderr

        escaping_directory = workspace / "escaping-directory"
        escaping_directory.symlink_to(Path("/tmp"), target_is_directory=True)
        symlink_structured = _wrapper_process(
            workspace,
            _structured_command(
                executable=sys.executable,
                args=[],
                cwd=escaping_directory,
            ),
        )
        _stdout, symlink_stderr = symlink_structured.communicate(timeout=5)
        assert symlink_structured.returncode == 64
        assert "outside allowed roots" in symlink_stderr

        child_pid_path = workspace / "child.pid"
        stubborn_program = (
            "import os, signal, time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "signal.signal(signal.SIGHUP, signal.SIG_IGN); "
            f'open({str(child_pid_path)!r}, "w").write(str(os.getpid())); '
            "time.sleep(300)"
        )
        stubborn_command = (
            f"{shlex.quote(sys.executable)} -c {shlex.quote(stubborn_program)}"
        )
        stubborn = _wrapper_process(workspace, stubborn_command)
        _wait_for_file(child_pid_path)
        child_pid = int(child_pid_path.read_text(encoding="utf-8"))
        assert stubborn.stdin is not None
        stubborn.stdin.close()
        stubborn.stdin = None
        stubborn.communicate(timeout=7)
        assert stubborn.returncode == 128 + signal.SIGHUP
        _assert_pid_gone(child_pid, "Forced-command descendant survived cancellation.")

        detached_pid_path = workspace / "detached.pid"
        detached_program = (
            "import os, signal, time; "
            "child=os.fork(); "
            "os.setsid() if child == 0 else None; "
            f'open({str(detached_pid_path)!r}, "w").write(str(os.getpid())) '
            "if child == 0 else None; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "signal.signal(signal.SIGHUP, signal.SIG_IGN); "
            "time.sleep(300)"
        )
        detached = _wrapper_process(
            workspace,
            f"{shlex.quote(sys.executable)} -c {shlex.quote(detached_program)}",
        )
        _wait_for_file(detached_pid_path)
        detached_pid = int(detached_pid_path.read_text(encoding="utf-8"))
        assert detached.stdin is not None
        detached.stdin.close()
        detached.stdin = None
        detached.communicate(timeout=8)
        assert detached.returncode == 128 + signal.SIGHUP
        _assert_pid_gone(
            detached_pid,
            "Session-detached descendant survived forced-command cancellation.",
        )

        background_pid_path = workspace / "background.pid"
        background_program = (
            "import subprocess; "
            'process=subprocess.Popen(["sleep", "300"], start_new_session=True); '
            f'open({str(background_pid_path)!r}, "w").write(str(process.pid))'
        )
        background = _wrapper_process(
            workspace,
            f"{shlex.quote(sys.executable)} -c {shlex.quote(background_program)}",
        )
        _wait_for_file(background_pid_path)
        background_pid = int(background_pid_path.read_text(encoding="utf-8"))
        background.wait(timeout=8)
        assert background.stdin is not None
        background.stdin.close()
        assert background.returncode == 0
        _assert_pid_gone(
            background_pid,
            "Session-detached background process survived command completion.",
        )

    print("advanced shell wrapper probe passed")


if __name__ == "__main__":
    main()
