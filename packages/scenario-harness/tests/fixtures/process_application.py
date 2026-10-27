"""Independent executable fixture for process supervision contracts."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from scenario_harness.process import supervise


def worker(root: Path, mode: str) -> None:
    if mode == "redact_child":
        os.write(1, b"descendant fixture-secret-value end\n")
        os.write(2, b"descendant fixture-secret-value end\n")
        return
    if mode == "redact":
        for fd in (1, 2):
            os.write(fd, b"split fixture-")
            time.sleep(0.03)
            os.write(fd, b"secret-value end\n")
        subprocess.run(
            [sys.executable, "-S", __file__, str(root), "redact_child"], check=True
        )
        return
    if mode == "child":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        (root / "child.pid").write_text(str(os.getpid()))
        while True:
            time.sleep(1)
    if mode in {"descendant_normal", "descendant_timeout", "interrupt"}:
        subprocess.Popen([sys.executable, "-S", __file__, str(root), "child"])
        while not (root / "child.pid").exists():
            time.sleep(0.01)
        if mode == "descendant_normal":
            return
        (root / "ready").write_text("ready")
    if mode == "ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if mode in {"block", "ignore_term", "descendant_timeout", "interrupt"}:
        while True:
            time.sleep(1)
    print("fixture stdout", flush=True)
    print("fixture stderr", file=sys.stderr, flush=True)
    if mode == "nonzero":
        raise SystemExit(7)


def main(root: Path, mode: str) -> None:
    if mode.startswith("worker_"):
        worker(root, mode.removeprefix("worker_"))
        return
    if mode in {"child", "redact_child"}:
        worker(root, mode)
        return
    if mode == "interrupt":

        def interrupt_when_ready() -> None:
            deadline = time.monotonic() + 10
            while not (root / "ready").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=interrupt_when_ready, daemon=True).start()
    timeout = 0.4 if mode in {"block", "ignore_term", "descendant_timeout"} else 10
    result = supervise(
        [sys.executable, "-S", __file__, str(root), "worker_" + mode],
        cwd=root,
        env=os.environ,
        artifacts_dir=root / "artifacts",
        timeout_seconds=timeout,
        interrupt_grace_seconds=0.2,
        termination_grace_seconds=0.2,
        redact_values=("fixture-secret-value",),
    )
    (root / "result.json").write_text(json.dumps(asdict(result)))


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
