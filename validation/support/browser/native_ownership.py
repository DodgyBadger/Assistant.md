"""Isolated native-owner fault probe; never scan or signal unrelated processes."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class _ProbeTerminated(Exception):
    pass


def _identity(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except FileNotFoundError:
        return None


async def _display_owner() -> None:
    from core.browser.engine import BrowserEngine

    engine = BrowserEngine()
    try:
        await engine._display()
        process = engine._display_process
        assert process is not None
        print(
            json.dumps({"pid": process.pid, "identity": _identity(process.pid)}),
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        await engine.close()


def _supervise() -> None:
    # Confine subreaping to this short-lived probe, never the pytest worker.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Cannot supervise native owner")
    environment = dict(os.environ)
    environment.pop("DISPLAY", None)

    def terminate(_signum, _frame):
        raise _ProbeTerminated("Native ownership probe interrupted")

    signal.signal(signal.SIGTERM, terminate)
    owner = None
    display = None
    reaped = False
    released = False
    try:
        owner = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "validation.support.browser.native_ownership",
                "owner",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert owner.stdout is not None
        display = json.loads(owner.stdout.readline())
        owner.kill()
        owner.wait(timeout=3)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            pid, _status = os.waitpid(display["pid"], os.WNOHANG)
            if pid:
                reaped = released = True
                break
            time.sleep(0.02)
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if owner is not None:
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=3)
        if display is not None and not reaped:
            # Only the exact child identity reported by our own owner is ours.
            if _identity(display["pid"]) == display["identity"]:
                os.kill(display["pid"], signal.SIGKILL)
            os.waitpid(display["pid"], 0)
            reaped = True
        if owner is not None and owner.stdout is not None:
            owner.stdout.close()
        # Group termination can interrupt owner publication. Reap any adopted
        # children without discovering or signalling a host process. SIGTERM
        # was already sent only to our isolated process group by the caller.
        deadline = time.monotonic() + 2
        while True:
            try:
                pid, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            if pid == 0:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Native probe child cleanup is unconfirmed")
                time.sleep(0.02)
    print(
        json.dumps(
            {"display_released_on_parent_loss": released, "test_cleanup_reaped": reaped}
        )
    )


if __name__ == "__main__":
    if sys.argv[1] == "owner":
        asyncio.run(_display_owner())
    else:
        _supervise()
