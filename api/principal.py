"""Interactive request principal resolution and request-scope installation."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, Request

from core.authentication import get_authenticated_identity
from core.identity import (
    LOCAL_USER_PRINCIPAL,
    ExecutionAuthority,
    Principal,
    use_execution_authority,
)


def resolve_request_principal(request: Request) -> Principal:
    """Resolve the authenticated identity to the fixed interactive principal."""
    identity = get_authenticated_identity(request.scope)
    if identity is None or identity.principal_id != LOCAL_USER_PRINCIPAL.principal_id:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return LOCAL_USER_PRINCIPAL


async def use_request_authority(
    principal: Principal = Depends(resolve_request_principal),
) -> AsyncIterator[ExecutionAuthority]:
    """Install resolved authority for the complete interactive request scope."""
    authority = ExecutionAuthority.from_principal(principal)
    with use_execution_authority(authority):
        yield authority
