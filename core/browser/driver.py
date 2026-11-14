"""Narrow adapter to the pinned Playwright pipe driver's owned process."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable, Coroutine
from importlib.metadata import version
from typing import Any

from playwright._impl._connection import Connection
from playwright._impl._object_factory import create_remote_object
from playwright._impl._playwright import Playwright as PlaywrightImplementation
from playwright.async_api import Playwright

from .operations import BrowserOperations, finish_cleanup
from .transport import BrowserPipeTransport


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
        verify_driver_version()
        if os.name != "posix":
            raise RuntimeError(
                "Managed browser driver requires a reviewed POSIX runtime"
            )
        loop = asyncio.get_running_loop()
        self._transport = BrowserPipeTransport(loop)
        self._connection = Connection(None, create_remote_object, self._transport, loop)
        self._connection_task: asyncio.Task[None] | None = None
        self._startup: asyncio.Task[Playwright] | None = None
        self._shutdown: asyncio.Task[None] | None = None

    async def start(self) -> Playwright:
        self._startup = asyncio.create_task(self._initialize())
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
        if self._shutdown is None:
            self._shutdown = asyncio.create_task(self._close(operations))
        await finish_cleanup(self._shutdown)

    async def _close(self, operations: BrowserOperations[None]) -> None:
        process: asyncio.subprocess.Process | None = None
        async with asyncio.timeout(2):
            while process is None:
                candidate = getattr(self._transport, "_proc", None)
                if isinstance(candidate, asyncio.subprocess.Process):
                    process = candidate
                else:
                    await asyncio.sleep(0.01)
        if self._startup is None:
            raise RuntimeError("Browser startup ownership is missing")
        try:
            await _stop_pipe_driver(self._connection.stop_async, process, operations)
        finally:
            # Once the actual subprocess has exited, an incomplete SDK init
            # waiter cannot produce a late live driver. Reap that waiter too.
            if process.returncode is not None and not self._startup.done():
                self._startup.cancel()
                await asyncio.gather(self._startup, return_exceptions=True)
        if self._connection_task is not None:
            _, pending = await asyncio.wait({self._connection_task}, timeout=1)
            if pending:
                raise RuntimeError("Browser connection cleanup is unconfirmed")

    async def _initialize(self) -> Playwright:
        self._connection_task = asyncio.create_task(self._connection.run())
        self._connection_task.add_done_callback(self._connection_completed)
        ready = self._connection.playwright_future
        done, _ = await asyncio.wait(
            {self._transport.on_error_future, ready},
            return_when=asyncio.FIRST_COMPLETED,
        )
        result = next(iter(done)).result()
        if not isinstance(result, PlaywrightImplementation):
            raise RuntimeError("Browser driver initialization failed")
        # Lifecycle belongs to this owner, never the API wrapper's no-op stop.
        return Playwright(result)

    @staticmethod
    def _connection_completed(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()
