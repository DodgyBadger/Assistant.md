"""Typed ingress-authentication contracts independent of FastAPI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from core.identity import LOCAL_USER_PRINCIPAL_ID


class AuthenticationMode(StrEnum):
    """Supported installation ingress-authentication policies."""

    DISABLED = "disabled"
    LOOPBACK = "loopback"
    TRUSTED_PROXY = "trusted_proxy"
    OWNER_TOKEN = "owner_token"


class AuthenticationMechanism(StrEnum):
    """Evidence that admitted one interactive request."""

    DISABLED = "disabled"
    LOOPBACK = "loopback"
    TRUSTED_PROXY = "trusted_proxy"
    OWNER_BEARER = "owner_bearer"
    OWNER_SESSION = "owner_session"


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Transport identity resolved before principal authority is installed."""

    principal_id: str
    mechanism: AuthenticationMechanism


def local_user_identity(mechanism: AuthenticationMechanism) -> AuthenticatedIdentity:
    """Return the fixed interactive identity for the single-user product."""
    return AuthenticatedIdentity(
        principal_id=LOCAL_USER_PRINCIPAL_ID,
        mechanism=mechanism,
    )
