"""Private viewport capture and ordered native input, without payload logging."""

from __future__ import annotations

import asyncio
import base64
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from playwright.async_api import CDPSession, Error

from .human_input import BrowserInputError, HumanInput
from .operations import BrowserOperations

_KEY_CODES = {
    "Tab": 9,
    "Enter": 13,
    "Backspace": 8,
    "Delete": 46,
    "ArrowLeft": 37,
    "ArrowRight": 39,
    "ArrowUp": 38,
    "ArrowDown": 40,
    "Home": 36,
    "End": 35,
    "Escape": 27,
}
Geometry = tuple[float, ...]


@dataclass(frozen=True)
class HumanFrame:
    jpeg: bytes
    page_id: str
    view_ref: str
    input_ref: str
    sequence: int
    width: int
    height: int
    frame_sequence: int


class HumanViewer:
    def __init__(
        self,
        connection: CDPSession,
        page_id: str,
        document_revision: Callable[[], int],
    ) -> None:
        self._connection = connection
        self._page_id = page_id
        self._revision = document_revision
        self._frames = BrowserOperations[tuple[bytes, Geometry]]()
        self._preparations = BrowserOperations[Geometry]()
        self._inputs = BrowserOperations[None]()
        self._releases = BrowserOperations[None]()
        self._generation: str | None = None
        self._surface: tuple[int, Geometry] | None = None
        self._view_ref: str | None = None
        self._input_ref: str | None = None
        self.sequence = 0
        self.frame_sequence = 0
        self.frame_pending = False
        self.input_pending = False
        self.unsafe = False

    def bind(self, generation: str) -> None:
        if self._generation != generation:
            self._generation = generation
            self.sequence = 0
            self.invalidate()

    def invalidate(self) -> None:
        self._surface = None
        self._view_ref = self._input_ref = None

    @property
    def can_return(self) -> bool:
        return (
            not self.input_pending
            and not self._inputs.pending
            and not self._preparations.pending
            and not self.unsafe
            and (self._generation is None or self._input_ref is not None)
        )

    @property
    def pending(self) -> bool:
        return (
            self._frames.pending or self._inputs.pending or self._preparations.pending
        )

    def _available(self) -> None:
        if self.unsafe:
            raise BrowserInputError("input_uncertain", retire=True)
        if self._inputs.pending or self._preparations.pending or self._frames.pending:
            raise BrowserInputError("browser_busy")

    async def geometry(self) -> Geometry:
        result = await self._connection.send("Page.getLayoutMetrics")
        viewport = result["cssVisualViewport"]
        values = tuple(
            float(viewport[key])
            for key in (
                "offsetX",
                "offsetY",
                "pageX",
                "pageY",
                "clientWidth",
                "clientHeight",
                "scale",
            )
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Invalid viewport")
        if (
            values[0:2] != (0, 0)
            or values[6] != 1
            or not 1 <= values[4] <= 1600
            or not 1 <= values[5] <= 1200
        ):
            raise ValueError("Unsupported viewport")
        return values

    async def _capture(self) -> tuple[bytes, Geometry]:
        before = await self.geometry()
        result = await self._connection.send(
            "Page.captureScreenshot",
            {
                "format": "jpeg",
                "quality": 55,
                "fromSurface": True,
                "captureBeyondViewport": False,
                "clip": {
                    "x": before[2],
                    "y": before[3],
                    "width": before[4],
                    "height": before[5],
                    "scale": 1,
                },
            },
        )
        after = await self.geometry()
        data = result["data"]
        if before != after or not isinstance(data, str) or len(data) > 2_666_668:
            raise ValueError("Unstable or excessive frame")
        jpeg = base64.b64decode(data, validate=True)
        if len(jpeg) > 2_000_000 or not jpeg.startswith(b"\xff\xd8"):
            raise ValueError("Invalid frame")
        return jpeg, before

    async def capture(self) -> HumanFrame:
        self._available()
        if self._frames.pending:
            raise BrowserInputError("browser_busy")
        revision = self._revision()
        try:
            async with asyncio.timeout(3):
                jpeg, geometry = await self._frames.run(self._capture())
            if self._revision() != revision:
                raise ValueError("Document changed")
            surface = (revision, geometry)
            if surface != self._surface:
                self.invalidate()
                self._surface = surface
            self._view_ref = self._view_ref or uuid4().hex
            self._input_ref = self._input_ref or uuid4().hex
            self.frame_sequence += 1
            return HumanFrame(
                jpeg,
                self._page_id,
                self._view_ref,
                self._input_ref,
                self.sequence,
                int(geometry[4]),
                int(geometry[5]),
                self.frame_sequence,
            )
        except asyncio.CancelledError:
            self.invalidate()
            raise
        except Exception:
            self.invalidate()
            raise BrowserInputError("frame_unavailable") from None

    async def prepare_input(self) -> Geometry:
        self._available()
        try:
            async with asyncio.timeout(1):
                return await self._preparations.run(self.geometry())
        except asyncio.CancelledError:
            self.invalidate()
            raise
        except Exception:
            self.invalidate()
            raise BrowserInputError("frame_unavailable") from None

    async def dispatch(
        self, command: HumanInput, geometry: Geometry
    ) -> dict[str, int | bool | str]:
        self._available()
        if self._surface != (self._revision(), geometry):
            self.invalidate()
            raise BrowserInputError("stale_input")
        if (
            command.page_id != self._page_id
            or command.view_ref != self._view_ref
            or command.input_ref != self._input_ref
            or command.sequence != self.sequence + 1
            or self._surface != (self._revision(), geometry)
            or (
                command.kind == "click"
                and (command.x >= geometry[4] or command.y >= geometry[5])
            )
        ):
            raise BrowserInputError("stale_input")
        self.sequence = command.sequence
        self.invalidate()
        try:
            async with asyncio.timeout(2):
                await self._inputs.run(self._dispatch_native(command, geometry))
        except asyncio.CancelledError:
            self.unsafe = command.kind in {"click", "key"}
            raise
        except Exception:
            self.unsafe = command.kind in {"click", "key"}
            raise BrowserInputError("input_uncertain", retire=self.unsafe) from None
        return {
            "status": "applied",
            "input_sequence": self.sequence,
            "requires_frame": True,
        }

    async def _send(self, method: str, parameters: dict[str, Any]) -> None:
        await self._connection.send(method, parameters)

    async def _dispatch_native(self, command: HumanInput, geometry: Geometry) -> None:
        if command.kind == "text":
            await self._send("Input.insertText", {"text": command.text})
        elif command.kind == "scroll":
            await self._send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": geometry[4] / 2,
                    "y": geometry[5] / 2,
                    "deltaX": 0,
                    "deltaY": command.delta_y,
                },
            )
        elif command.kind == "click":
            coordinates = {
                "x": command.x,
                "y": command.y,
                "button": "left",
                "clickCount": 1,
            }
            try:
                await self._send(
                    "Input.dispatchMouseEvent",
                    {**coordinates, "type": "mousePressed", "buttons": 1},
                )
            finally:
                await self._send(
                    "Input.dispatchMouseEvent",
                    {**coordinates, "type": "mouseReleased", "buttons": 0},
                )
        elif command.key == "SelectAll":
            control = {
                "key": "Control",
                "code": "ControlLeft",
                "windowsVirtualKeyCode": 17,
            }
            letter = {"key": "a", "code": "KeyA", "windowsVirtualKeyCode": 65}
            try:
                await self._send(
                    "Input.dispatchKeyEvent",
                    {**control, "type": "keyDown", "modifiers": 2},
                )
                await self._send(
                    "Input.dispatchKeyEvent",
                    {**letter, "type": "keyDown", "modifiers": 2},
                )
            finally:
                try:
                    await self._send(
                        "Input.dispatchKeyEvent",
                        {**letter, "type": "keyUp", "modifiers": 2},
                    )
                finally:
                    await self._send(
                        "Input.dispatchKeyEvent",
                        {**control, "type": "keyUp", "modifiers": 0},
                    )
        else:
            parameters: dict[str, Any] = {
                "key": command.key,
                "code": command.key,
                "windowsVirtualKeyCode": _KEY_CODES[command.key],
            }
            try:
                await self._send(
                    "Input.dispatchKeyEvent",
                    {
                        **parameters,
                        "type": "keyDown",
                        **({"text": "\r"} if command.key == "Enter" else {}),
                    },
                )
            finally:
                await self._send(
                    "Input.dispatchKeyEvent", {**parameters, "type": "keyUp"}
                )

    async def close(self) -> None:
        self.invalidate()
        try:
            async with asyncio.timeout(2):
                await self._releases.run(self._connection.detach())
        except Error:
            pass
