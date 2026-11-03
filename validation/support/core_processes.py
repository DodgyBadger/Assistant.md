"""Accidental-dispatch guard for core tests, not an OS sandbox.

Trusted fixtures may declare exact child commands. The app may use its bounded
ripgrep search and pinned loopback curl transport against fixture-owned inputs.
An admitted native executable can still access the host: no child/native egress
or credential-file containment is claimed. Use a disposable, secret-free runtime.
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import sys
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit


class CoreProcessDenied(PermissionError):
    """A core scenario attempted an undeclared process launch."""


_installed = False
_lock = RLock()
_commands: set[tuple[str, ...]] = set()
_roots: set[Path] = set()
_tools = {
    name: Path(p).resolve() for name in ("rg", "curl") if (p := shutil.which(name))
}


def declare_core_command(command: list[str]) -> list[str]:
    """Declare an exact trusted-fixture command, without replacing its execution."""
    with _lock:
        _commands.add(tuple(command))
    return command


def reset_core_commands(root: Path | None = None) -> None:
    with _lock:
        _commands.clear()
        _roots.clear()
        if root is not None:
            _roots.add(root.resolve())


def _owned(path: str) -> bool:
    candidate = Path(path).resolve()
    return any(candidate.is_relative_to(root) for root in _roots)


def _bounded_tool(command: tuple[str, ...]) -> bool:
    executable = shutil.which(command[0])
    if executable is None:
        return False
    resolved = Path(executable).resolve()
    if resolved == _tools.get("rg"):
        prefix = (
            "--json",
            "--fixed-strings",
            "--ignore-case",
            "--max-columns",
            "500",
            "--max-filesize",
            "2M",
            "--",
        )
        return (
            command[1:9] == prefix
            and len(command) > 10
            and all(_owned(p) for p in command[10:])
        )
    if resolved != _tools.get("curl"):
        return False
    flags = {"--silent", "--show-error", "--http1.1"}
    values = {
        "--proto",
        "--noproxy",
        "--max-time",
        "--connect-timeout",
        "--max-filesize",
        "--resolve",
        "--dump-header",
        "--output",
        "--header",
        "--write-out",
    }
    options: dict[str, str] = {}
    index = 1
    while index < len(command) - 1:
        option = command[index]
        if option in flags:
            index += 1
        elif option in values and index + 1 < len(command) - 1:
            if option in options and option != "--header":
                return False
            options[option] = command[index + 1]
            index += 2
        else:
            return False
    try:
        url = urlsplit(command[-1])
        pinned_host, pinned_port, pinned_ip = options["--resolve"].split(":", 2)
        safe_destination = ipaddress.ip_address(pinned_ip.strip("[]")).is_loopback
        safe_outputs = all(
            Path(options[key]).parent.name.startswith("assistantmd-urlfetch-")
            for key in ("--output", "--dump-header")
        )
        return (
            safe_destination
            and safe_outputs
            and url.scheme in {"http", "https"}
            and not url.username
            and not url.password
            and pinned_host == url.hostname
            and int(pinned_port) == (url.port or (443 if url.scheme == "https" else 80))
            and options["--proto"] == "=http,https"
            and options["--noproxy"] == "*"
            and 0 < float(options["--max-time"]) <= 300
            and 0 < float(options["--connect-timeout"]) <= 300
            and 0 < int(options["--max-filesize"]) <= 25 * 1024 * 1024
        )
    except (ValueError, KeyError):
        return False


def _audit(event: str, args: tuple[object, ...]) -> None:
    if event in {"os.system", "os.exec"}:
        raise CoreProcessDenied(
            "Core scenarios cannot launch shell or exec replacements"
        )
    if event not in {"subprocess.Popen", "os.posix_spawn"}:
        return
    executable, raw = args[:2]
    if not isinstance(raw, list | tuple) or not raw:
        raise CoreProcessDenied("Core subprocesses require a declared argument vector")
    command = tuple(os.fsdecode(value) for value in raw)
    with _lock:
        # Check the actual executable as well as argv[0]; Popen(executable=...)
        # must not turn an admitted argv into a different program.
        actual = shutil.which(os.fsdecode(executable))
        expected = shutil.which(command[0])
        if actual and expected and Path(actual).resolve() == Path(expected).resolve():
            if command in _commands or _bounded_tool(command):
                return
    raise CoreProcessDenied("Core subprocess launch was not declared by a fixture")


def install_core_process_guard() -> None:
    global _installed
    if not _installed:
        sys.addaudithook(_audit)
        _installed = True
