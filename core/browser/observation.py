"""Bounded isolated-world observations and single-use native DOM actions."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from playwright.async_api import CDPSession, Error
from pydantic import Field, TypeAdapter, ValidationError

from .operations import BrowserOperations

ActionOperation = Literal["click", "fill", "select", "scroll"]
_SCRIPT = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")


class BrowserInteractionError(RuntimeError):
    """Only bounded domain reasons may cross the action/logging boundary."""

    def __init__(self, reason: str, message: str | None = None) -> None:
        self.reason = reason
        if message is None:
            if reason in {"action_uncertain", "browser_unresponsive"}:
                message = f"Browser operation completion is uncertain ({reason}); inspect the website state before another write. Do not repeat the action automatically."
            else:
                message = f"Browser interaction {reason}; read a fresh observation before retrying"
        super().__init__(message)


class BrowserReadDeadline(BrowserInteractionError):
    """A navigation readiness deadline, with service-attested retained state."""

    def __init__(
        self,
        browser_id: str,
        page_id: str,
        retained_url: str,
        *,
        page_retained: bool,
        help_eligible: bool,
    ) -> None:
        self.browser_id = browser_id
        self.page_id = page_id
        self.retained_url = retained_url
        self.page_retained = page_retained
        self.help_eligible = help_eligible and page_retained
        message = (
            "Browser read deadline; read the current page without a URL to inspect retained state before retrying navigation."
            if page_retained
            else "Browser read deadline; no usable page was retained. Open a new URL to continue."
        )
        super().__init__("read_deadline", message)

    def metadata(self) -> dict[str, str | bool]:
        data: dict[str, str | bool] = {
            "page_retained": self.page_retained,
            "help_eligible": self.help_eligible,
        }
        if self.page_retained:
            data.update(
                browser_id=self.browser_id,
                page_id=self.page_id,
                retained_url=self.retained_url,
            )
        return data


class BrowserActionUncertain(BrowserInteractionError):
    """A dispatched action may have completed; its reference cannot be reused."""

    def __init__(self) -> None:
        super().__init__("action_uncertain")

    def metadata(self) -> dict[str, bool]:
        return {
            "help_eligible": False,
            "completion_uncertain": True,
            "requires_inspection": True,
        }


@dataclass(frozen=True)
class BrowserDocument:
    frame_id: str
    loader_id: str
    web_document: bool


@dataclass(frozen=True)
class BrowserOption:
    value: Annotated[str, Field(max_length=80)]
    label: Annotated[str, Field(max_length=80)]
    disabled: bool
    selected: bool


@dataclass(frozen=True)
class BrowserElement:
    ref: Annotated[str, Field(max_length=128)]
    tag: Annotated[str, Field(max_length=16)]
    label: Annotated[str, Field(max_length=120)]
    options: Annotated[list[BrowserOption], Field(max_length=60)]
    checked: bool | None


@dataclass(frozen=True)
class PageView:
    observation_ref: Annotated[str, Field(max_length=128)]
    text: Annotated[str, Field(max_length=12000)]
    elements: Annotated[list[BrowserElement], Field(max_length=60)]
    truncated: bool


_VIEW = TypeAdapter(PageView)


class PageObservation:
    def __init__(self, connection: CDPSession, browser_id: str, page_id: str) -> None:
        self._connection = connection
        self._messages = BrowserOperations[dict[str, Any]]()
        self._releases = BrowserOperations[None]()
        self._identity = f"{browser_id}.{page_id}"
        self._prefix: str | None = None
        self._group: str | None = None
        self._object: str | None = None
        self.broken = False

    async def invalidate(self) -> None:
        try:
            await self._dispose_snapshot()
        except (Error, TimeoutError):
            self.broken = True
            raise BrowserInteractionError("browser_unresponsive") from None
        except asyncio.CancelledError:
            self.broken = True
            raise

    async def _dispose_snapshot(self) -> None:
        group, object_id = self._group, self._object
        self._prefix = self._group = self._object = None
        if group is None:
            return
        primary: BaseException | None = None
        try:
            if object_id is not None:
                async with asyncio.timeout(1):
                    await self._send(
                        "Runtime.callFunctionOn",
                        {
                            "objectId": object_id,
                            "functionDeclaration": "function() { this.dispose(); }",
                            "returnByValue": True,
                        },
                    )
        except Error:
            # Navigation destroys the isolated execution world and its observer.
            pass
        except BaseException as exc:
            primary = exc
        try:
            async with asyncio.timeout(1):
                await self._send("Runtime.releaseObjectGroup", {"objectGroup": group})
        except asyncio.CancelledError:
            if not isinstance(primary, asyncio.CancelledError):
                raise
        except (Error, TimeoutError):
            if primary is None:
                raise
        if primary is not None:
            raise primary

    async def read(self) -> PageView:
        try:
            async with asyncio.timeout(3):
                return await self._read()
        except TimeoutError:
            self.broken = True
            raise BrowserInteractionError("browser_unresponsive") from None
        except asyncio.CancelledError:
            self.broken = True
            raise
        except BrowserInteractionError:
            self.broken = True
            raise

    async def _read(self) -> PageView:
        await self.invalidate()
        self._prefix = f"{self._identity}.{uuid4().hex}"
        self._group = uuid4().hex
        try:
            tree = await self._send("Page.getFrameTree")
            world = await self._send(
                "Page.createIsolatedWorld",
                {
                    "frameId": tree["frameTree"]["frame"]["id"],
                    "worldName": "AssistantMD observations",
                    "grantUniveralAccess": False,
                },
            )
            result = await self._send(
                "Runtime.evaluate",
                {
                    "expression": f"({_SCRIPT})({json.dumps(self._prefix)})",
                    "contextId": world["executionContextId"],
                    "objectGroup": self._group,
                },
            )
            self._object = result.get("result", {}).get("objectId")
            if not self._object or "exceptionDetails" in result:
                raise BrowserInteractionError("observation_failed")
            result = await self._send(
                "Runtime.callFunctionOn",
                {
                    "objectId": self._object,
                    "functionDeclaration": "function() { return this.view; }",
                    "returnByValue": True,
                },
            )
            return _VIEW.validate_python(result["result"]["value"])
        except (Error, KeyError, ValidationError):
            raise BrowserInteractionError("observation_failed") from None

    async def act(
        self, operation: ActionOperation, reference: str, value: str, scroll_y: int
    ) -> bool:
        prefix = self._prefix
        if prefix is None or self._object is None or not reference.startswith(prefix):
            raise BrowserInteractionError("stale_reference")
        if operation == "scroll":
            if reference != prefix:
                raise BrowserInteractionError("stale_reference")
            index = None
        else:
            suffix = reference.removeprefix(prefix + ".")
            if (
                not reference.startswith(prefix + ".")
                or not suffix.isascii()
                or not suffix.isdecimal()
                or len(suffix) > 2
            ):
                raise BrowserInteractionError("stale_reference")
            index = int(suffix)
        self._prefix = None
        primary: BaseException | None = None
        try:
            async with asyncio.timeout(3):
                response = await self._send(
                    "Runtime.callFunctionOn",
                    {
                        "objectId": self._object,
                        "functionDeclaration": "function(request) { return this.act(request); }",
                        "arguments": [
                            {
                                "value": {
                                    "operation": operation,
                                    "index": index,
                                    "value": value,
                                    "scroll_y": scroll_y,
                                }
                            }
                        ],
                        "returnByValue": True,
                    },
                )
            reason = response.get("result", {}).get("value")
            if isinstance(reason, str) and reason in {"completed", "navigating"}:
                return reason == "navigating"
            if not isinstance(reason, str) or reason not in {
                "stale_reference",
                "sensitive_target",
                "blocked_target",
                "invalid_value",
                "unsupported_target",
            }:
                reason = "action_uncertain"
            raise BrowserInteractionError(reason)
        except Error:
            raise BrowserInteractionError("action_uncertain") from None
        except TimeoutError:
            try:
                await self.stop_loading()
            except BaseException as exc:
                primary = exc
                raise
            raise BrowserInteractionError("action_uncertain") from None
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                await self.invalidate()
            except asyncio.CancelledError:
                if not isinstance(primary, asyncio.CancelledError):
                    raise
            except BrowserInteractionError:
                if not isinstance(primary, asyncio.CancelledError):
                    raise

    async def close(self) -> None:
        try:
            if not self.broken:
                await self.invalidate()
        except BrowserInteractionError:
            pass
        finally:
            try:
                async with asyncio.timeout(2):
                    await self._releases.run(self._connection.detach())
            except Error:
                pass

    async def _send(
        self, method: str, parameters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await self._messages.run(self._connection.send(method, parameters))

    async def stop_loading(self) -> None:
        try:
            async with asyncio.timeout(1):
                await self._send("Page.stopLoading")
        except (Error, TimeoutError):
            self.broken = True
            raise BrowserInteractionError("browser_unresponsive") from None
        except asyncio.CancelledError:
            self.broken = True
            raise

    async def document_identity(self) -> BrowserDocument:
        """A history/hash event does not change the committed loader identity."""
        try:
            async with asyncio.timeout(1):
                result = await self._send("Page.getFrameTree")
            frame = result["frameTree"]["frame"]
            frame_id, loader_id = frame["id"], frame["loaderId"]
            if (
                not isinstance(frame_id, str)
                or not isinstance(loader_id, str)
                or len(frame_id) > 128
                or len(loader_id) > 128
            ):
                raise ValueError("Invalid browser document identity")
            url = frame.get("url")
            web_document = (
                isinstance(url, str)
                and url.startswith(("http://", "https://"))
                and not frame.get("unreachableUrl")
            )
            return BrowserDocument(frame_id, loader_id, bool(web_document))
        except (Error, TimeoutError, KeyError, TypeError, ValueError):
            self.broken = True
            raise BrowserInteractionError("browser_unresponsive") from None
        except asyncio.CancelledError:
            self.broken = True
            raise
