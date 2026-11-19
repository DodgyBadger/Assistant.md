"""Owned sandboxed Chromium process and optional local display."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from tempfile import TemporaryFile

from playwright.async_api import Browser

from core.logger import UnifiedLogger

from .driver import BrowserDriver
from .environment import browser_environment
from .operations import BrowserOperations, finish_cleanup


class BrowserEngine:
    def __init__(self) -> None:
        self.stack = AsyncExitStack()
        self.browser: Browser | None = None
        self._operations = BrowserOperations[None]()
        self._closing: asyncio.Task[None] | None = None
        self._driver_started = False
        self._driver_released = False
        self._released = False
        self._display_process: asyncio.subprocess.Process | None = None

    async def start(self, proxy: str) -> Browser:
        try:
            display = await self._display()
            driver = BrowserDriver()
            self._driver_started = True
            self.stack.push_async_callback(self._stop_driver, driver)
            playwright = await driver.start()
            environment: dict[str, str | float | bool] = dict(
                browser_environment(
                    display=display, temporary_directory=driver.temporary_directory
                )
            )
            browser = await playwright.chromium.launch(
                channel="chromium",
                headless=False,
                chromium_sandbox=True,
                proxy={"server": proxy, "bypass": "<-loopback>"},
                args=[
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                ],
                env=environment,
                timeout=10000,
            )
            self.stack.push_async_callback(self._close_browser, browser)
            self.browser = browser
            browser.on("disconnected", self._on_disconnected)
            UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).info(
                "Browser engine ready",
                data={"event": "browser_engine_started", "status": "completed"},
            )
            return browser
        except BaseException as error:
            try:
                await self.close()
            except BaseException as cleanup_error:
                error.add_note(
                    f"Browser startup cleanup failed: {type(cleanup_error).__name__}"
                )
            raise

    @staticmethod
    def _on_disconnected(_browser: Browser) -> None:
        UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).info(
            "Browser engine disconnected",
            data={"event": "browser_engine_disconnected", "status": "completed"},
        )

    async def _display(self) -> str | None:
        current = os.environ.get("DISPLAY")
        if current or platform.system() != "Linux":
            return current
        xvfb = shutil.which("Xvfb")
        if xvfb is None:
            raise RuntimeError(
                "Browser display support is missing; install Xvfb during setup"
            )
        # Use a private pipe for display allocation, with no fixed display or
        # global environment mutation. The runtime owns its process and errors.
        read_fd, write_fd = os.pipe()
        errors = self.stack.enter_context(TemporaryFile())
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(Path(__file__).with_name("native_child.py").resolve()),
                str(os.getpid()),
                str(Path(xvfb).resolve(strict=True)),
                "-displayfd",
                str(write_fd),
                "-screen",
                "0",
                "1280x800x24",
                "-nolisten",
                "tcp",
                pass_fds=(write_fd,),
                env=browser_environment(),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=errors,
            )
            self._display_process = process
            self.stack.push_async_callback(_stop_display, process)
            os.close(write_fd)
            write_fd = -1
            pipe = os.fdopen(read_fd, "rb", buffering=0)
            read_fd = -1
            self.stack.enter_context(pipe)
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(reader)
            transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                lambda: protocol, pipe
            )
            self.stack.callback(transport.close)
            async with asyncio.timeout(5):
                value = (await reader.readline()).decode().strip()
            if not value.isdecimal():
                raise RuntimeError(
                    "Browser display could not start; check Xvfb permissions and libraries"
                )
            return ":" + value
        finally:
            if read_fd >= 0:
                os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)

    async def close(self) -> None:
        if self._released:
            self.browser = None
            return
        if self._closing is None:
            self._closing = asyncio.create_task(self._close_resources())
        await finish_cleanup(self._closing)
        self.browser = None

    async def _close_resources(self) -> None:
        try:
            await self.stack.aclose()
        finally:
            self._released = (not self._driver_started or self._driver_released) and (
                self._display_process is None
                or self._display_process.returncode is not None
            )
        if not self._released:
            raise RuntimeError("Browser process cleanup is unconfirmed")

    async def _stop_driver(self, driver: BrowserDriver) -> None:
        await driver.close(self._operations)
        self._driver_released = True

    async def _close_browser(self, browser: Browser) -> None:
        async with asyncio.timeout(2):
            await self._operations.run(browser.close())


async def _stop_display(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
        try:
            async with asyncio.timeout(3):
                await process.wait()
        except TimeoutError:
            process.kill()
            async with asyncio.timeout(3):
                await process.wait()
