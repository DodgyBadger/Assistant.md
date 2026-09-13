#!/usr/bin/env python3
"""Run one fixed-destination SSH command with bounded process ownership."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from ctypes import CDLL, get_errno
from pathlib import Path

WORKSPACE_ROOT = Path("/workspace")
TERMINATION_GRACE_SECONDS = 2.0
POLL_INTERVAL_SECONDS = 0.05
FORWARDED_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
PR_SET_CHILD_SUBREAPER = 36
STRUCTURED_STDIO_PREFIX = "assistantmd-stdio-v1:"
MAX_STRUCTURED_STDIO_ENVELOPE_BYTES = 64 * 1024
MAX_ARGUMENTS = 64
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_ENVIRONMENT_VALUES = 16
MAX_ENVIRONMENT_VALUE_BYTES = 4096
ALLOWED_WORKING_ROOTS = (
    Path("/workspace"),
    Path("/home/advanced-shell"),
)
ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
RESERVED_ENVIRONMENT_NAMES = frozenset(
    {
        "HOME",
        "LANG",
        "LC_ALL",
        "PATH",
        "NPM_CONFIG_PREFIX",
        "SHELL",
        "TMPDIR",
        "UV_TOOL_BIN_DIR",
        "UV_TOOL_DIR",
    }
)


def _execution_environment() -> dict[str, str]:
    """Return the complete, intentionally small command environment."""
    return {
        "HOME": "/home/advanced-shell",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/home/advanced-shell/.local/bin:/usr/local/bin:/usr/bin:/bin",
        "NPM_CONFIG_PREFIX": "/home/advanced-shell/.local",
        "SHELL": "/bin/bash",
        "TMPDIR": "/tmp",
        "UV_TOOL_BIN_DIR": "/home/advanced-shell/.local/bin",
        "UV_TOOL_DIR": "/home/advanced-shell/.local/share/uv/tools",
    }


def _decode_structured_launch(
    command: str,
) -> tuple[list[str], Path, dict[str, str]] | None:
    """Decode one bounded stdio launch without involving a shell parser."""
    if not command.startswith(STRUCTURED_STDIO_PREFIX):
        return None
    encoded = command.removeprefix(STRUCTURED_STDIO_PREFIX)
    if not encoded or len(encoded) > MAX_STRUCTURED_STDIO_ENVELOPE_BYTES * 2:
        raise ValueError("Structured stdio launch envelope is invalid.")
    try:
        raw = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (UnicodeEncodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("Structured stdio launch envelope is invalid.") from exc
    if len(raw) > MAX_STRUCTURED_STDIO_ENVELOPE_BYTES or not isinstance(payload, dict):
        raise ValueError("Structured stdio launch envelope is invalid.")
    if set(payload) != {"executable", "args", "cwd", "env"}:
        raise ValueError("Structured stdio launch fields are invalid.")

    executable = payload["executable"]
    arguments = payload["args"]
    cwd_value = payload["cwd"]
    environment_values = payload["env"]
    if (
        not isinstance(executable, str)
        or not executable
        or "\x00" in executable
        or not Path(executable).is_absolute()
        or ".." in Path(executable).parts
    ):
        raise ValueError("Structured stdio executable must be an absolute path.")
    if (
        not isinstance(arguments, list)
        or len(arguments) > MAX_ARGUMENTS
        or not all(
            isinstance(argument, str) and "\x00" not in argument
            for argument in arguments
        )
        or sum(len(argument.encode()) for argument in arguments) > MAX_ARGUMENT_BYTES
    ):
        raise ValueError("Structured stdio arguments are invalid.")
    if not isinstance(cwd_value, str) or not cwd_value:
        raise ValueError("Structured stdio working directory is invalid.")
    cwd = Path(cwd_value)
    if (
        ".." in cwd.parts
        or not cwd.is_absolute()
        or not any(
            cwd == root or cwd.is_relative_to(root) for root in ALLOWED_WORKING_ROOTS
        )
    ):
        raise ValueError("Structured stdio working directory is outside allowed roots.")
    try:
        resolved_cwd = cwd.resolve(strict=True)
        resolved_roots = tuple(
            root.resolve(strict=True) for root in ALLOWED_WORKING_ROOTS
        )
    except OSError as exc:
        raise ValueError("Structured stdio working directory is unavailable.") from exc
    if not any(
        resolved_cwd == root or resolved_cwd.is_relative_to(root)
        for root in resolved_roots
    ):
        raise ValueError("Structured stdio working directory is outside allowed roots.")
    if (
        not isinstance(environment_values, dict)
        or len(environment_values) > MAX_ENVIRONMENT_VALUES
    ):
        raise ValueError("Structured stdio environment is invalid.")
    environment = _execution_environment()
    for name, value in environment_values.items():
        if (
            not isinstance(name, str)
            or ENVIRONMENT_NAME_PATTERN.fullmatch(name) is None
            or name in RESERVED_ENVIRONMENT_NAMES
            or not isinstance(value, str)
            or "\x00" in value
            or len(value.encode()) > MAX_ENVIRONMENT_VALUE_BYTES
        ):
            raise ValueError("Structured stdio environment is invalid.")
        environment[name] = value
    return [executable, *arguments], resolved_cwd, environment


def _signal_process_group(process: subprocess.Popen[bytes], signum: int) -> None:
    """Forward a signal to the owned process group when it still exists."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signum)
    except ProcessLookupError:
        return


def _enable_child_subreaper() -> None:
    """Reparent daemonized descendants here instead of container PID 1."""
    libc = CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error_number = get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _descendant_pids(root_pid: int) -> set[int]:
    """Return the current recursive process descendants of one PID."""
    children_by_parent: dict[int, set[int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        parent_pid: int | None = None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                parent_pid = int(line.split(":", 1)[1].strip())
                break
        if parent_pid is not None:
            children_by_parent.setdefault(parent_pid, set()).add(int(entry.name))

    descendants: set[int] = set()
    pending = list(children_by_parent.get(root_pid, set()))
    while pending:
        pid = pending.pop()
        if pid in descendants:
            continue
        descendants.add(pid)
        pending.extend(children_by_parent.get(pid, set()))
    return descendants


def _signal_descendants(root_pid: int, signum: int) -> None:
    """Signal descendants individually, including new sessions/process groups."""
    for pid in sorted(_descendant_pids(root_pid), reverse=True):
        try:
            os.kill(pid, signum)
        except ProcessLookupError:
            continue


def _reap_children() -> None:
    """Reap exited descendants adopted through subreaper ownership."""
    while True:
        try:
            waited_pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if waited_pid == 0:
            return


def _settle_descendants(root_pid: int, grace_seconds: float) -> None:
    """Terminate every remaining descendant and wait for the tree to settle."""
    deadline = time.monotonic() + grace_seconds
    _signal_descendants(root_pid, signal.SIGTERM)
    while time.monotonic() < deadline:
        _reap_children()
        if not _descendant_pids(root_pid):
            return
        time.sleep(POLL_INTERVAL_SECONDS)
    _signal_descendants(root_pid, signal.SIGKILL)
    settle_deadline = time.monotonic() + 1.0
    while time.monotonic() < settle_deadline:
        _reap_children()
        if not _descendant_pids(root_pid):
            return
        time.sleep(POLL_INTERVAL_SECONDS)


def _forward_stdin(
    process: subprocess.Popen[bytes], channel_closed: threading.Event
) -> None:
    """Forward SSH input while retaining EOF as a channel-lifetime signal."""
    if process.stdin is None:
        channel_closed.set()
        return
    try:
        while chunk := os.read(sys.stdin.fileno(), 65536):
            process.stdin.write(chunk)
            process.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        channel_closed.set()


def run_command(command: str) -> int:
    """Run the requested shell command and own its complete process group."""
    if not command.strip():
        print("AssistantMD advanced shell requires a command.", file=sys.stderr)
        return 64
    if "\x00" in command:
        print(
            "AssistantMD advanced shell command contains invalid data.", file=sys.stderr
        )
        return 64
    if not WORKSPACE_ROOT.is_dir():
        print("AssistantMD advanced shell workspace is unavailable.", file=sys.stderr)
        return 72

    _enable_child_subreaper()
    wrapper_pid = os.getpid()

    try:
        structured_launch = _decode_structured_launch(command)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 64
    argv, cwd, environment = (
        structured_launch
        if structured_launch is not None
        else (["/bin/bash", "-lc", command], WORKSPACE_ROOT, _execution_environment())
    )

    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=sys.stdout.buffer,
        stderr=sys.stderr.buffer,
        start_new_session=True,
    )
    channel_closed = threading.Event()
    threading.Thread(
        target=_forward_stdin,
        args=(process, channel_closed),
        name="advanced-shell-stdin",
        daemon=True,
    ).start()
    shutdown_signal: int | None = None
    shutdown_deadline: float | None = None

    def begin_shutdown(signum: int) -> None:
        nonlocal shutdown_deadline, shutdown_signal
        if shutdown_signal is None:
            shutdown_signal = signum
            shutdown_deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
            _signal_process_group(process, signal.SIGTERM)
            _signal_descendants(wrapper_pid, signal.SIGTERM)

    def request_shutdown(signum: int, _frame: object) -> None:
        begin_shutdown(signum)

    previous_handlers = {
        signum: signal.signal(signum, request_shutdown) for signum in FORWARDED_SIGNALS
    }
    try:
        while True:
            return_code = process.poll()
            if return_code is not None:
                _settle_descendants(wrapper_pid, TERMINATION_GRACE_SECONDS)
                if shutdown_signal is not None:
                    return 128 + shutdown_signal
                return return_code
            if channel_closed.is_set() and shutdown_signal is None:
                begin_shutdown(signal.SIGHUP)
            if shutdown_deadline is not None and time.monotonic() >= shutdown_deadline:
                _signal_process_group(process, signal.SIGKILL)
                _signal_descendants(wrapper_pid, signal.SIGKILL)
                process.wait()
                _settle_descendants(wrapper_pid, 0.25)
                return 128 + (shutdown_signal or signal.SIGTERM)
            time.sleep(POLL_INTERVAL_SECONDS)
    finally:
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)
        if process.poll() is None:
            _signal_process_group(process, signal.SIGKILL)
            _signal_descendants(wrapper_pid, signal.SIGKILL)
            process.wait()
        _settle_descendants(wrapper_pid, 0.25)


def main() -> int:
    """Read OpenSSH's forced-command contract and execute it."""
    return run_command(os.environ.get("SSH_ORIGINAL_COMMAND", ""))


if __name__ == "__main__":
    raise SystemExit(main())
