"""Execution-authority policy for the single-user advanced shell."""

from __future__ import annotations

from core.identity import LOCAL_USER_PRINCIPAL_ID, ExecutionAuthority


def advanced_shell_authority_allowed(authority: ExecutionAuthority) -> bool:
    """Return whether an authority may use the deployment-owned advanced shell."""
    return authority.principal_id == LOCAL_USER_PRINCIPAL_ID


def require_advanced_shell_authority(authority: ExecutionAuthority) -> None:
    """Reject authorities unsupported by the single-user advanced shell."""
    if not advanced_shell_authority_allowed(authority):
        raise PermissionError(
            "Advanced-shell execution is unavailable for this principal."
        )
