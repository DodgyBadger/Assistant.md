"""Instance-scoped environment and silent protocol for pinned Playwright 1.58."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

import playwright
from playwright._impl._helper import ParsedMessagePayload
from playwright._impl._transport import PipeTransport

from .environment import browser_environment
from .storage import BrowserTemporaryStorage


class BrowserPipeTransport(PipeTransport):
    """Keep the SDK framing/run loop; own process launch and protocol privacy."""

    def __init__(
        self, loop: asyncio.AbstractEventLoop, storage: BrowserTemporaryStorage
    ) -> None:
        super().__init__(loop)
        self._storage = storage

    async def connect(self) -> None:
        self._stopped_future = asyncio.get_running_loop().create_future()
        if os.name != "posix":
            raise RuntimeError(
                "Managed browser transport requires a reviewed POSIX runtime"
            )
        package = Path(playwright.__file__).parent / "driver"
        environment = browser_environment(temporary_directory=self._storage.path)
        environment.update(
            PW_LANG_NAME="python",
            PW_LANG_NAME_VERSION=f"{sys.version_info.major}.{sys.version_info.minor}",
            PW_CLI_DISPLAY_VERSION="1.58.0",
        )
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(package / "node"),
                str(package / "package" / "cli.js"),
                "run-driver",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=32768,
                env=environment,
            )
        except Exception as error:
            self.on_error_future.set_exception(error)
            raise
        self._output = self._proc.stdin

    def serialize_message(self, message: dict[str, Any]) -> bytes:
        # Parent DEBUGP must never print text, cookies or captured frames.
        return json.dumps(message).encode("utf-8")

    def deserialize_message(self, data: str | bytes) -> ParsedMessagePayload:
        message = json.loads(data)
        if not isinstance(message, dict):
            raise RuntimeError("Invalid browser driver protocol message")
        # The pinned SDK dispatches this decoded wire dictionary and validates
        # its method-specific payload. Preserve that external protocol boundary.
        return cast(ParsedMessagePayload, message)
