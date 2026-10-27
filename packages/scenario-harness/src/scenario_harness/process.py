"""Run a command in an isolated, bounded process group."""

from __future__ import annotations

import math
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class ProcessOutcome:
    returncode: int
    timed_out: bool
    interrupted: bool
    elapsed: float


def _signal_group(process: subprocess.Popen[bytes], sig: signal.Signals) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, sig)
        elif process.poll() is None:
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.terminate()
    except ProcessLookupError:
        pass


def _group_exists(process: subprocess.Popen[bytes]) -> bool:
    if os.name != "posix":
        return process.poll() is None
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    return True


def _stop_group(
    process: subprocess.Popen[bytes], sig: signal.Signals, grace: float
) -> None:
    _signal_group(process, sig)
    deadline = time.monotonic() + grace
    while _group_exists(process):
        process.poll()  # Reap the leader as soon as possible.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.05, remaining))
    _signal_group(process, signal.SIGKILL)


class _Redactor:
    def __init__(self, values: Sequence[str]) -> None:
        self.values = sorted(
            {v.encode("utf-8") for v in values if v}, key=len, reverse=True
        )
        self.tail = b""
        self.overlap = max((len(v) for v in self.values), default=1) - 1

    def feed(self, data: bytes, *, final: bool = False) -> bytes:
        if not self.values:
            return data
        data = self.tail + data
        limit = len(data) if final else max(0, len(data) - self.overlap)
        output = bytearray()
        index = 0
        while index < limit:
            match = next((v for v in self.values if data.startswith(v, index)), None)
            if match is not None:
                output.extend(b"[REDACTED]")
                index += len(match)
            else:
                output.append(data[index])
                index += 1
        self.tail = data[index:]
        return bytes(output)


def _capture(
    stream: BinaryIO,
    destination: BinaryIO,
    values: Sequence[str],
    stop: threading.Event,
    errors: list[BaseException],
) -> None:
    try:
        os.set_blocking(stream.fileno(), False)
        redactor = _Redactor(values)
        stop_deadline: float | None = None
        while True:
            if stop.is_set() and stop_deadline is None:
                stop_deadline = time.monotonic() + 0.25
            if stop_deadline is not None and time.monotonic() >= stop_deadline:
                break
            try:
                data = os.read(stream.fileno(), 65536)
            except BlockingIOError:
                if stop.wait(0.01):
                    break
                continue
            if not data:
                break
            destination.write(redactor.feed(data))
        destination.write(redactor.feed(b"", final=True))
        destination.flush()
    except BaseException as exc:
        errors.append(exc)
    finally:
        stream.close()


def supervise(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    artifacts_dir: Path,
    timeout_seconds: float | None,
    interrupt_grace_seconds: float = 2,
    termination_grace_seconds: float = 2,
    redact_values: Sequence[str] = (),
) -> ProcessOutcome:
    """Capture a command's output and bound its execution and descendants.

    On POSIX the command receives a new session and its entire process group is
    signalled. Descendants that deliberately leave that group are outside this
    supervisor's control. Supplied values are redacted as UTF-8 byte sequences
    from both captured streams.
    """
    if timeout_seconds is not None and (
        not math.isfinite(timeout_seconds) or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be finite and positive or None")
    if not math.isfinite(interrupt_grace_seconds) or interrupt_grace_seconds < 0:
        raise ValueError("interrupt_grace_seconds must be finite and nonnegative")
    if not math.isfinite(termination_grace_seconds) or termination_grace_seconds < 0:
        raise ValueError("termination_grace_seconds must be finite and nonnegative")
    if not command:
        raise ValueError("command must not be empty")

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    timed_out = False
    interrupted = False
    with (
        (artifacts_dir / "stdout.txt").open("wb") as stdout,
        (artifacts_dir / "stderr.txt").open("wb") as stderr,
    ):
        stop = threading.Event()
        errors: list[BaseException] = []
        started_readers: list[threading.Thread] = []
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        try:
            assert process.stdout is not None and process.stderr is not None
            for source, target in ((process.stdout, stdout), (process.stderr, stderr)):
                reader = threading.Thread(
                    target=_capture,
                    args=(source, target, redact_values, stop, errors),
                    daemon=True,
                )
                reader.start()
                started_readers.append(reader)
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                _stop_group(process, signal.SIGTERM, termination_grace_seconds)
            except KeyboardInterrupt:
                interrupted = True
                _stop_group(process, signal.SIGINT, interrupt_grace_seconds)
        finally:
            # A normally exiting leader may leave children behind. Kill the
            # group before reaping; no lifecycle cleanup is implied by this.
            _signal_group(process, signal.SIGKILL)
            process.wait()
            stop.set()
            for reader in started_readers:
                reader.join()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        if errors:
            raise RuntimeError("Failed to capture process output") from errors[0]
    return ProcessOutcome(
        process.returncode, timed_out, interrupted, time.monotonic() - started
    )
