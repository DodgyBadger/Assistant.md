"""Narrow synchronous compatibility API for encrypted principal-owned secrets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload

from core.identity import (
    SYSTEM_AUTHORITY,
    ExecutionAuthority,
    get_current_execution_authority,
)
from core.runtime.paths import get_system_root
from core.secrets import get_encrypted_secrets_service
from core.secrets.legacy_migration import DEFAULT_NAMESPACE

_OPENAI_OAUTH_NAMESPACE = "oauth.openai"
_OPENAI_OAUTH_NAMES = frozenset(
    {"OPENAI_OAUTH_PENDING_STATE", "OPENAI_OAUTH_TOKEN_STATE"}
)
_SYSTEM_SECRET_NAMES = frozenset({"LOGFIRE_TOKEN"})


@dataclass(frozen=True)
class SecretEntry:
    """Metadata about a stored secret without exposing values."""

    name: str
    has_value: bool
    is_overlay: bool = False


@overload
def load_secrets(*, include_empty: Literal[True]) -> dict[str, str | None]: ...


@overload
def load_secrets(*, include_empty: Literal[False] = False) -> dict[str, str]: ...


def load_secrets(
    *, include_empty: bool = False
) -> dict[str, str] | dict[str, str | None]:
    """Load current-principal generic configuration secrets."""
    authority = _authority_for_name(None)
    service = get_encrypted_secrets_service()
    values = {
        item.name: service.get_for_authority(authority, DEFAULT_NAMESPACE, item.name)
        for item in service.list_metadata_for_authority(authority, DEFAULT_NAMESPACE)
    }
    if include_empty:
        return values
    return {name: value for name, value in values.items() if value is not None}


def list_secret_entries() -> list[SecretEntry]:
    """Return current-principal generic secret metadata."""
    authority = _authority_for_name(None)
    service = get_encrypted_secrets_service()
    return [
        SecretEntry(name=item.name, has_value=True, is_overlay=True)
        for item in service.list_metadata_for_authority(authority, DEFAULT_NAMESPACE)
    ]


def get_secret_value(name: str) -> str | None:
    """Return a secret for its explicit system or current-principal owner."""
    if not name:
        return None
    authority = _authority_for_name(name)
    return get_encrypted_secrets_service().get_for_authority(
        authority, _namespace_for_name(name), name
    )


def set_secret_value(name: str, value: str | None) -> None:
    """Create, replace, or clear an encrypted secret."""
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("Secret name cannot be empty.")
    authority = _authority_for_name(clean_name)
    service = get_encrypted_secrets_service()
    normalized = (value or "").strip()
    if not normalized:
        service.delete_for_authority(
            authority, _namespace_for_name(clean_name), clean_name
        )
        return
    service.set_for_authority(
        authority,
        _namespace_for_name(clean_name),
        clean_name,
        normalized,
    )


def remove_secret(name: str) -> None:
    """Clear a secret value by deleting its encrypted record."""
    delete_secret(name)


def delete_secret(name: str) -> None:
    """Delete a secret entry entirely from encrypted storage."""
    clean_name = str(name or "").strip()
    if not clean_name:
        return
    get_encrypted_secrets_service().delete_for_authority(
        _authority_for_name(clean_name),
        _namespace_for_name(clean_name),
        clean_name,
    )


def secret_has_value(name: str) -> bool:
    """Return True when an encrypted secret exists and is non-empty."""
    return bool(get_secret_value(name))


def ensure_secrets_file() -> Path:
    """Ensure encrypted storage is ready and return its managed database path."""
    get_encrypted_secrets_service()
    return get_system_root() / "access.db"


def _namespace_for_name(name: str) -> str:
    return _OPENAI_OAUTH_NAMESPACE if name in _OPENAI_OAUTH_NAMES else DEFAULT_NAMESPACE


def _authority_for_name(name: str | None) -> ExecutionAuthority:
    if name in _SYSTEM_SECRET_NAMES:
        return SYSTEM_AUTHORITY
    authority = get_current_execution_authority()
    if authority is None:
        raise RuntimeError("Execution authority is required for secret access.")
    return authority
