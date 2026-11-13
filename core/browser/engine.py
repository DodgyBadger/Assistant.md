"""Owned sandboxed Chromium process and optional local display."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from contextlib import AsyncExitStack
from tempfile import TemporaryFile

from playwright.async_api import Browser, async_playwright

from core.logger import UnifiedLogger


class BrowserEngine:
    def __init__(self) -> None:
        self.stack = AsyncExitStack()
        self.browser: Browser | None = None

    async def start(self, proxy: str) -> Browser:
        try:
            display = await self._display()
            playwright = await self.stack.enter_async_context(async_playwright())
            browser = await playwright.chromium.launch(
                channel="chromium",
                headless=False,
                chromium_sandbox=True,
                proxy={"server": proxy, "bypass": "<-loopback>"},
                args=[
                    "--disable-quic",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                ],
                env={**os.environ, **({"DISPLAY": display} if display else {})},
                timeout=10000,
            )
            self.stack.push_async_callback(browser.close)
            self.browser = browser
            UnifiedLogger(tag="browser-runtime").set_sinks(["validation"]).info(
                "Browser engine ready",
                data={"event": "browser_engine_started", "status": "completed"},
            )
            return browser
        except BaseException as error:
            try:
                async with asyncio.timeout(5):
                    await self.stack.aclose()
            except BaseException as cleanup_error:
                error.add_note(
                    f"Browser startup cleanup failed: {type(cleanup_error).__name__}"
                )
            raise

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
                xvfb,
                "-displayfd",
                str(write_fd),
                "-screen",
                "0",
                "1280x800x24",
                "-nolisten",
                "tcp",
                pass_fds=(write_fd,),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=errors,
            )
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
        await self.stack.aclose()
        self.browser = None


async def _stop_display(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
        try:
            async with asyncio.timeout(3):
                await process.wait()
        except TimeoutError:
            process.kill()
            await process.wait()
