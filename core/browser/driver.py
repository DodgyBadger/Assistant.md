"""Narrow adapter to the pinned Playwright pipe driver's owned process."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable, Coroutine
from importlib.metadata import version
from pathlib import Path
from typing import Any

from playwright._impl._connection import Connection
from playwright._impl._object_factory import create_remote_object
from playwright._impl._playwright import Playwright as PlaywrightImplementation
from playwright.async_api import Playwright

from .operations import BrowserOperations, finish_cleanup
from .storage import BrowserTemporaryStorage
from .transport import BrowserPipeTransport


def verify_driver_version() -> None:
    if version("playwright") != "1.58.0":
        raise RuntimeError("Browser driver ownership adapter needs version review")


async def _stop_pipe_driver(
    stop: Callable[[], Coroutine[Any, Any, None]],
    process: asyncio.subprocess.Process,
    operations: BrowserOperations[None],
    *,
    api_exposed: bool,
) -> None:
    exited_before_stop = process.returncode is not None
    terminated_by_owner = False
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
                terminated_by_owner = True
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
    # Before the API is exposed, no caller can have requested a browser.
    # The exact owned child may still be stopped before installing handlers.
    confirmed_preinitialization_stop = (
        terminated_by_owner
        and not api_exposed
        and process.returncode == -signal.SIGTERM
    )
    if exited_before_stop or (
        process.returncode != 0 and not confirmed_preinitialization_stop
    ):
        raise RuntimeError(
            "Unexpected browser driver exit; child cleanup is unconfirmed"
        )


class BrowserDriver:
    """Own initialization before the SDK can spawn its pipe subprocess."""

    def __init__(self) -> None:
        verify_driver_version()
        if os.name != "posix":
            raise RuntimeError(
                "Managed browser driver requires a reviewed POSIX runtime"
            )
        loop = asyncio.get_running_loop()
        self._api_exposed = False
        self._storage = BrowserTemporaryStorage()
        self._transport = BrowserPipeTransport(loop, self._storage)
        self._connection = Connection(None, create_remote_object, self._transport, loop)
        self._connection_task: asyncio.Task[None] | None = None
        self._startup: asyncio.Task[Playwright] | None = None
        self._shutdown: asyncio.Task[None] | None = None

    @property
    def temporary_directory(self) -> Path:
        return self._storage.path

    async def start(self) -> Playwright:
        self._storage.create()
        self._startup = asyncio.create_task(self._initialize())
        self._startup.add_done_callback(self._completed)
        async with asyncio.timeout(5):
            playwright = await asyncio.shield(self._startup)
        self._api_exposed = True
        return playwright

    @staticmethod
    def _completed(task: asyncio.Task[Playwright]) -> None:
        if not task.cancelled():
            task.exception()

    @property
    def cleanup_confirmed(self) -> bool:
        return (
            self._shutdown is not None
            and self._shutdown.done()
            and not self._shutdown.cancelled()
            and self._shutdown.exception() is None
        )

    async def close(self, operations: BrowserOperations[None]) -> None:
        if self._startup is None:
            await self._storage.close()
            return
        if self._shutdown is None:
            self._shutdown = asyncio.create_task(self._close(operations))
        await finish_cleanup(self._shutdown)

    async def _close(self, operations: BrowserOperations[None]) -> None:
        process: asyncio.subprocess.Process | None = None
        try:
            async with asyncio.timeout(2):
                while process is None:
                    candidate = getattr(self._transport, "_proc", None)
                    if isinstance(candidate, asyncio.subprocess.Process):
                        process = candidate
                    elif (
                        self._connection_task is not None
                        and self._connection_task.done()
                    ):
                        await self._settle_unpublished_startup()
                        await self._storage.close()
                        return
                    else:
                        await asyncio.sleep(0.01)
        except TimeoutError:
            # Publication can race the deadline. Check before cancellation:
            # there is no yield between this check and cancelling run/connect.
            candidate = getattr(self._transport, "_proc", None)
            if isinstance(candidate, asyncio.subprocess.Process):
                process = candidate
            else:
                await self._settle_unpublished_startup()
                await self._storage.close()
                return
        if self._startup is None:
            raise RuntimeError("Browser startup ownership is missing")
        try:
            await _stop_pipe_driver(
                self._connection.stop_async,
                process,
                operations,
                api_exposed=self._api_exposed,
            )
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
        await self._storage.close()

    async def _settle_unpublished_startup(self) -> None:
        # asyncio's subprocess transport owns native cleanup while spawning.
        # Cancelling and confirming that real operation's settlement prevents
        # a delayed spawn from publishing a child after this owner has closed.
        connection = self._connection_task
        if connection is not None:
            connection.cancel()
            _, pending = await asyncio.wait({connection}, timeout=2)
            if pending:
                raise RuntimeError("Browser pending spawn cleanup is unconfirmed")
            if not connection.cancelled():
                connection.exception()
        if isinstance(
            getattr(self._transport, "_proc", None), asyncio.subprocess.Process
        ):
            raise RuntimeError("Browser process appeared during startup cancellation")
        if self._startup is not None:
            self._startup.cancel()
            _, pending_initialization = await asyncio.wait({self._startup}, timeout=1)
            if pending_initialization:
                raise RuntimeError("Browser initialization cleanup is unconfirmed")
            if not self._startup.cancelled():
                self._startup.exception()

    async def _initialize(self) -> Playwright:
        self._connection_task = asyncio.create_task(self._connection.run())
        self._connection_task.add_done_callback(self._connection_completed)
        ready = self._connection.playwright_future
        done, _ = await asyncio.wait(
            {self._transport.on_error_future, ready, self._connection_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if self._transport.on_error_future in done:
            self._transport.on_error_future.result()
        if ready not in done:
            self._connection_task.result()
            raise RuntimeError("Browser driver exited before initialization")
        result = ready.result()
        if not isinstance(result, PlaywrightImplementation):
            raise RuntimeError("Browser driver initialization failed")
        # Lifecycle belongs to this owner, never the API wrapper's no-op stop.
        return Playwright(result)

    @staticmethod
    def _connection_completed(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()
