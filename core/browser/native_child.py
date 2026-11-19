"""Standalone Linux exec guard for an app-owned native browser dependency."""

from __future__ import annotations

import ctypes
import os
import signal
import sys


def main() -> None:
    if sys.platform != "linux" or len(sys.argv) < 3:
        raise RuntimeError("Native browser child ownership requires Linux")
    parent = int(sys.argv[1])
    if parent < 1 or os.getppid() != parent:
        raise RuntimeError("Native browser child owner already exited")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), "Native browser parent-loss guard failed")
    # Close the race where the parent exits before prctl installs the signal.
    if os.getppid() != parent:
        raise RuntimeError("Native browser child owner exited during startup")
    executable = sys.argv[2]
    if not os.path.isabs(executable):
        raise RuntimeError("Native browser dependency must have an absolute path")
    os.execv(executable, sys.argv[2:])


if __name__ == "__main__":
    main()
