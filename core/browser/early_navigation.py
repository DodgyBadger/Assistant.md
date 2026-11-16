"""Narrow Playwright 1.58 identity adapter for uninitialized popup navigation."""

from __future__ import annotations

from playwright._impl._connection import from_nullable_channel
from playwright._impl._frame import Frame as FrameImpl
from playwright.async_api import Frame, Request


def frame_key(frame: Frame) -> str:
    implementation = frame._impl_obj
    if not isinstance(implementation, FrameImpl):
        raise RuntimeError("Unsupported browser frame identity")
    return implementation._guid


def early_frame_key(request: Request) -> str | None:
    """Read SDK identity only; never infer page ownership from a URL/header."""
    frame = from_nullable_channel(request._impl_obj._initializer.get("frame"))
    if not isinstance(frame, FrameImpl) or frame._page is not None:
        return None
    if frame.parent_frame is not None or not request.is_navigation_request():
        return None
    return frame._guid
