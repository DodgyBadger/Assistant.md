"""Authenticated handoff control and private bounded browser I/O."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from core.browser.handoff import HandoffView
from core.browser.human_input import BrowserInputError, HumanInput
from core.browser.observation import BrowserInteractionError
from core.browser.service import BrowserAdmissionError
from core.identity import AuthorizationError
from core.logger import UnifiedLogger
from core.runtime.state import get_runtime_context

from .principal import use_request_authority

router = APIRouter(
    prefix="/api/chat/sessions", dependencies=[Depends(use_request_authority)]
)


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin is not None and origin != expected
    ):
        raise HTTPException(
            status_code=403, detail="Same-origin control request required"
        )


async def result(operation: Awaitable[HandoffView]) -> dict[str, str]:
    try:
        return (await operation).as_metadata()
    except AuthorizationError:
        raise HTTPException(
            status_code=404, detail="Browser handoff unavailable"
        ) from None
    except (BrowserAdmissionError, BrowserInteractionError) as error:
        status = 404 if error.reason in {"no_handoff", "no_current_page"} else 409
        raise HTTPException(status_code=status, detail=error.reason) from None
    except TimeoutError:
        raise HTTPException(status_code=409, detail="Browser handoff busy") from None


@router.get("/{session_id}/browser-handoff")
async def handoff_state(session_id: str) -> dict[str, str]:
    return await result(get_runtime_context().browser.handoff_state(session_id))


@router.post("/{session_id}/browser-handoff/{handoff_id}/claim")
async def claim_handoff(
    session_id: str, handoff_id: str, request: Request
) -> dict[str, str]:
    require_same_origin(request)
    return await result(
        get_runtime_context().browser.claim_handoff(session_id, handoff_id)
    )


@router.post("/{session_id}/browser-handoff/{handoff_id}/return")
async def return_handoff(
    session_id: str, handoff_id: str, request: Request
) -> dict[str, str]:
    require_same_origin(request)
    return await result(
        get_runtime_context().browser.return_handoff(session_id, handoff_id)
    )


async def read_private_input(request: Request) -> HumanInput:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        != "application/json"
    ):
        raise BrowserInputError("invalid_input", status_code=400)
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise BrowserInputError("invalid_input", status_code=400)
        if len(length) > 5 or int(length) > 8192:
            raise BrowserInputError("input_too_large", status_code=413)
    raw = bytearray()
    try:
        async with asyncio.timeout(2):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 8192:
                    raise BrowserInputError("input_too_large", status_code=413)
                raw.extend(chunk)
    except TimeoutError:
        raise BrowserInputError("invalid_input", status_code=400) from None
    return HumanInput.decode(bytes(raw))


def private_error(error: Exception) -> JSONResponse:
    if isinstance(error, BrowserInputError):
        return JSONResponse(
            {
                "detail": error.reason,
                "requires_frame": True,
                "requires_fresh_session": error.retire,
            },
            status_code=error.status_code,
        )
    if isinstance(error, AuthorizationError):
        return JSONResponse({"detail": "no_handoff"}, status_code=404)
    if isinstance(error, BrowserAdmissionError | BrowserInteractionError):
        return JSONResponse(
            {"detail": error.reason},
            status_code=404 if error.reason == "no_handoff" else 409,
        )
    UnifiedLogger(tag="browser-private-io").error(
        "Private browser operation failed",
        data={"event": "browser_private_io_failed", "error_type": type(error).__name__},
    )
    # No SDK exception, validation input or frame bytes may cross this boundary.
    return JSONResponse({"detail": "browser_io_unavailable"}, status_code=503)


@router.get("/{session_id}/browser-handoff/{handoff_id}/frame")
async def browser_frame(session_id: str, handoff_id: str) -> Response:
    try:
        frame = await get_runtime_context().browser.frame_handoff(
            session_id, handoff_id
        )
        return Response(
            frame.jpeg,
            media_type="image/jpeg",
            headers={
                "X-Browser-Page": frame.page_id,
                "X-Browser-Root": frame.root_page_id,
                "X-Browser-Popup": frame.popup_page_id,
                "X-Browser-Window": frame.window_kind,
                "X-Browser-Popup-Notice": frame.popup_notice,
                "X-Browser-Selection": str(frame.selection_revision),
                "X-Browser-Origin": frame.site_origin,
                "X-Browser-View": frame.view_ref,
                "X-Browser-Input": frame.input_ref,
                "X-Browser-Sequence": str(frame.sequence),
                "X-Browser-Frame": str(frame.frame_sequence),
                "X-Content-Type-Options": "nosniff",
                "X-Browser-Width": str(frame.width),
                "X-Browser-Height": str(frame.height),
                "Cross-Origin-Resource-Policy": "same-origin",
                "Referrer-Policy": "no-referrer",
            },
        )
    except Exception as error:
        return private_error(error)


@router.post("/{session_id}/browser-handoff/{handoff_id}/input")
async def browser_input(session_id: str, handoff_id: str, request: Request) -> Response:
    require_same_origin(request)
    try:
        response = await get_runtime_context().browser.input_handoff(
            session_id, handoff_id, lambda: read_private_input(request)
        )
        return JSONResponse(response)
    except Exception as error:
        return private_error(error)


@router.post("/{session_id}/browser-handoff/{handoff_id}/page/{page_id}")
async def select_browser_page(
    session_id: str, handoff_id: str, page_id: str, request: Request
) -> Response:
    require_same_origin(request)
    try:
        response = await get_runtime_context().browser.select_handoff_page(
            session_id, handoff_id, page_id
        )
        return JSONResponse(response)
    except Exception as error:
        return private_error(error)
