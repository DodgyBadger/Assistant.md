"""Start an optional virtual display in the existing development container."""

from __future__ import annotations

import os
import select
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def browser_runtime(mode: str) -> Iterator[None]:
    """Use installed dependencies, or the already-extracted investigation runtime."""
    root = Path(
        os.environ.get(
            "BROWSER_EXPERIMENT_RUNTIME_ROOT", "/tmp/assistantmd-browser-deps/root"
        )
    )
    xvfb = shutil.which("Xvfb")
    changes: dict[str, str] = {}
    fallback = xvfb is None and root.is_dir()
    if fallback:
        libraries = [*root.glob("usr/lib/*-linux-gnu"), *root.glob("lib/*-linux-gnu")]
        changes["LD_LIBRARY_PATH"] = ":".join(
            [*(str(path) for path in libraries), os.environ.get("LD_LIBRARY_PATH", "")]
        )
        fonts = root.parent / "fonts.conf"
        if fonts.is_file():
            changes["FONTCONFIG_FILE"] = str(fonts)
        binary = root / "usr/bin/Xvfb-local"
        if not binary.is_file():
            binary = root / "usr/bin/Xvfb"
        xvfb = str(binary) if binary.is_file() else None
    previous = {key: os.environ.get(key) for key in (*changes, "DISPLAY")}
    process: subprocess.Popen[bytes] | None = None
    try:
        os.environ.update(changes)
        if mode != "headless" and not os.environ.get("DISPLAY"):
            if xvfb is None:
                raise RuntimeError(
                    "Headful browsing needs an existing DISPLAY or Xvfb installed in this container"
                )
            read_fd, write_fd = os.pipe()
            try:
                command = [
                    xvfb,
                    "-displayfd",
                    str(write_fd),
                    "-screen",
                    "0",
                    "1440x1000x24",
                    "-nolisten",
                    "tcp",
                ]
                if fallback:
                    command.extend(
                        [
                            "-xkbdir",
                            str(root / "usr/share/X11/xkb"),
                            "-fp",
                            str(root / "usr/share/fonts/X11/misc"),
                        ]
                    )
                process = subprocess.Popen(
                    command,
                    pass_fds=(write_fd,),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                os.close(write_fd)
                write_fd = -1
                if not select.select([read_fd], [], [], 10)[0]:
                    raise TimeoutError("Virtual display did not become ready")
                display = os.read(read_fd, 64).decode().strip()
                if not display.isdecimal():
                    raise RuntimeError(
                        "Virtual display could not start; check the Xvfb dependencies"
                    )
                os.environ["DISPLAY"] = ":" + display
            finally:
                os.close(read_fd)
                if write_fd >= 0:
                    os.close(write_fd)
        yield
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
