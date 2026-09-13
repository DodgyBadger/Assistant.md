"""Connection-backed built-in capability availability."""

from __future__ import annotations

from enum import StrEnum

from core.identity import require_current_execution_authority


class ConnectionRequirement(StrEnum):
    """Typed connection requirements declared by settings-backed tools."""

    GOOGLE_GMAIL_READ = "google.gmail.read"


def connection_requirement_available(requirement: ConnectionRequirement) -> bool:
    """Resolve one requirement from current runtime and execution authority."""
    from core.integrations.google import GoogleCapability
    from core.runtime.state import get_runtime_context, has_runtime_context

    if not has_runtime_context():
        return False
    runtime = get_runtime_context()
    google = runtime.google_connection
    if google is None:
        return False
    authority = require_current_execution_authority()
    if requirement is ConnectionRequirement.GOOGLE_GMAIL_READ:
        return google.any_capability_available(authority, GoogleCapability.GMAIL_READ)
    return False
