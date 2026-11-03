"""Observe and pause one real revision-checked settings save without replacing it."""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager


class SaveBarrier:
    def __init__(self, code):
        self.code = code
        self.entered = threading.Event()
        self.release = threading.Event()
        self.timed_out = threading.Event()
        self.claim = threading.Lock()

    def profile(self, frame, event, arg):
        del arg
        if event != "call" or frame.f_code is not self.code:
            return
        with self.claim:
            if self.entered.is_set():
                return
            self.entered.set()
        if not self.release.wait(10):
            self.timed_out.set()


@contextmanager
def pause_next_settings_save():
    from core.settings.repository import save

    if not hasattr(threading, "setprofile_all_threads"):
        raise RuntimeError("Python does not support process-wide thread profiling")
    if threading.getprofile() is not None or sys.getprofile() is not None:
        raise RuntimeError("Settings save barrier cannot replace an active profiler")
    barrier = SaveBarrier(save.__code__)
    threading.setprofile_all_threads(barrier.profile)
    try:
        yield barrier
    finally:
        barrier.release.set()
        threading.setprofile_all_threads(None)
        sys.setprofile(None)
