"""Narrow adapter to the pinned Playwright pipe driver's owned process."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable, Coroutine
from importlib.metadata import version
from typing import Any

from playwright.async_api import Playwright

from .operations import BrowserOperations


def verify_driver_version() -> None:
    if version("playwright") != "1.58.0":
        raise RuntimeError("Browser driver ownership adapter needs version review")


async def _stop_pipe_driver(
    stop: Callable[[], Coroutine[Any, Any, None]],
    process: asyncio.subprocess.Process,
    operations: BrowserOperations[None],
) -> None:
    try:
        async with asyncio.timeout(2):
            await operations.run(stop())
    except TimeoutError:
        if process.returncode is None:
            # Resume only our exact stopped child so its own signal/pipe-exit
            # cleanup can close and reap Chromium before any forced kill.
            try:
                if os.name == "posix":
                    process.send_signal(signal.SIGCONT)
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                async with asyncio.timeout(2):
                    await process.wait()
            except TimeoutError:
                process.kill()
                async with asyncio.timeout(2):
                    await process.wait()
                raise RuntimeError(
                    "Forced browser driver exit; child cleanup is unconfirmed"
                ) from None
    if not await operations.settled():
        raise RuntimeError("Browser driver IPC cleanup is unconfirmed")


class BrowserDriver:
    """Own initialization before the SDK can spawn its pipe subprocess."""

    def __init__(self) -> None:
        from playwright.async_api import async_playwright

        verify_driver_version()
        self._manager = async_playwright()
        self._startup: asyncio.Task[Playwright] | None = None

    async def start(self) -> Playwright:
        self._startup = asyncio.create_task(self._manager.start())
        self._startup.add_done_callback(self._completed)
        async with asyncio.timeout(5):
            return await asyncio.shield(self._startup)

    @staticmethod
    def _completed(task: asyncio.Task[Playwright]) -> None:
        if not task.cancelled():
            task.exception()

    async def close(self, operations: BrowserOperations[None]) -> None:
        if self._startup is None:
            return
        process: asyncio.subprocess.Process | None = None
        # The pinned manager creates its connection before awaiting driver
        # initialization. Poll only this owner's transport during partial start.
        async with asyncio.timeout(2):
            while process is None:
                connection = getattr(self._manager, "_connection", None)
                transport = getattr(connection, "_transport", None)
                candidate = getattr(transport, "_proc", None)
                if isinstance(candidate, asyncio.subprocess.Process):
                    process = candidate
                else:
                    await asyncio.sleep(0.01)
        try:
            await _stop_pipe_driver(self._manager.__aexit__, process, operations)
        finally:
            # Once the actual subprocess has exited, an incomplete SDK init
            # waiter cannot produce a late live driver. Reap that waiter too.
            if process.returncode is not None and not self._startup.done():
                self._startup.cancel()
                await asyncio.gather(self._startup, return_exceptions=True)
