"""Authenticated metadata-only handoff control; no human input or CDP ingress."""

from __future__ import annotations

from collections.abc import Awaitable

from fastapi import APIRouter, Depends, HTTPException, Request

from core.browser.handoff import HandoffView
from core.browser.observation import BrowserInteractionError
from core.browser.service import BrowserAdmissionError
from core.identity import AuthorizationError
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
