"""
Utility functions for API operations.
"""

import os
import re
import traceback
from datetime import datetime
from typing import Any

from fastapi.responses import JSONResponse

from core.logger import UnifiedLogger
from core.settings.store import get_general_settings
from core.tools.failures import FailureClassification, classify_exception

from .exceptions import APIException, InvalidVaultNameError
from .models import ErrorResponse

# Create API logger
logger = UnifiedLogger(tag="api")


def serialize_exception(exception: Exception) -> dict:
    """Return structured exception details safe for activity logging."""
    return {
        "error_type": type(exception).__name__,
        "error": str(exception),
        "traceback": "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        ).strip(),
    }


def generate_session_id(vault_name: str) -> str:
    """
    Generate meaningful session ID from vault and timestamp.

    Format: {vault_name}_{YYYYMMDD_HHMMSS}
    Example: MyVault_20251002_143022

    Args:
        vault_name: Vault name to include in session ID

    Returns:
        Session ID string with format {vault_name}_{timestamp}
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Sanitize vault name for filesystem safety
    safe_vault_name = vault_name.replace(" ", "_").replace("/", "_")
    return f"{safe_vault_name}_{timestamp}"


def validate_vault_name(vault_name: str) -> str:
    """
    Validate and clean a vault name for filesystem safety.

    Args:
        vault_name: Raw vault name from user input

    Returns:
        Cleaned vault name safe for filesystem use

    Raises:
        InvalidVaultNameError: If vault name is invalid
    """
    if not vault_name or not vault_name.strip():
        raise InvalidVaultNameError(vault_name, "Vault name cannot be empty")

    # Clean the name
    cleaned = vault_name.strip()

    # Check length
    if len(cleaned) > 100:
        raise InvalidVaultNameError(
            vault_name, "Vault name too long (max 100 characters)"
        )

    if len(cleaned) < 1:
        raise InvalidVaultNameError(vault_name, "Vault name too short")

    # Check for invalid characters (allow alphanumeric, hyphens, underscores, spaces)
    if not re.match(r"^[a-zA-Z0-9\-_\s]+$", cleaned):
        raise InvalidVaultNameError(
            vault_name,
            "Vault name contains invalid characters. Only letters, numbers, hyphens, underscores, and spaces are allowed",
        )

    # Check for reserved names
    reserved_names = {"con", "prn", "aux", "nul", "logs", "system"}
    if cleaned.lower() in reserved_names:
        raise InvalidVaultNameError(vault_name, f"'{cleaned}' is a reserved name")

    # Check that it doesn't start or end with spaces or special chars
    if cleaned != cleaned.strip():
        raise InvalidVaultNameError(
            vault_name, "Vault name cannot start or end with spaces"
        )

    # Replace spaces with hyphens for filesystem safety
    filesystem_safe = re.sub(r"\s+", "-", cleaned)

    return filesystem_safe


def create_error_response(exception: Exception) -> JSONResponse:
    """
    Create a standardized error response from an exception.

    Args:
        exception: The exception that occurred

    Returns:
        JSONResponse with standardized error format
    """
    if isinstance(exception, APIException):
        details = build_api_error_details(exception)
        error_response = ErrorResponse(
            success=False,
            error=exception.error_type,
            message=exception.detail,
            details=details,
        )
        _log_api_error_response(
            error_type=exception.error_type,
            status_code=exception.status_code,
            details=details,
        )
        return JSONResponse(
            status_code=exception.status_code, content=error_response.model_dump()
        )
    else:
        # Handle unexpected exceptions
        # Include detailed traceback in development/debug mode
        settings = get_general_settings()
        debug_setting = settings.get("debug")
        debug_mode = bool(debug_setting and getattr(debug_setting, "value", False))
        exception_details = serialize_exception(exception)
        safe_details = build_api_error_details(exception)

        if debug_mode:
            error_response = ErrorResponse(
                success=False,
                error="InternalServerError",
                message=str(exception),
                details={
                    **safe_details,
                    "traceback": exception_details["traceback"],
                },
            )
        else:
            # Generic error for production
            error_response = ErrorResponse(
                success=False,
                error="InternalServerError",
                message="An unexpected error occurred",
                details=safe_details,
            )

        # Always log the full traceback server-side
        logger.error(
            "Unexpected API error",
            data=exception_details,
        )
        _log_api_error_response(
            error_type="InternalServerError",
            status_code=500,
            details=safe_details,
        )

        return JSONResponse(status_code=500, content=error_response.model_dump())


def build_api_error_details(exception: Exception) -> dict[str, Any]:
    """Build stable agent-safe API error details."""
    if isinstance(exception, APIException):
        base_details = dict(exception.details or {})
        classification = _classify_api_exception(exception, base_details=base_details)
        details = {**base_details, **classification.to_metadata()}
        details["error_type"] = exception.error_type
        details["http_status"] = exception.status_code
        return details

    classification = classify_exception(exception, phase="api_request")
    classified_details: dict[str, Any] = dict(classification.to_metadata())
    classified_details["error_type"] = type(exception).__name__
    classified_details.setdefault("http_status", 500)
    return classified_details


def _classify_api_exception(
    exception: APIException,
    *,
    base_details: dict[str, Any],
) -> FailureClassification:
    phase = str(base_details.get("phase") or "api_request")
    retryable = (
        _api_status_retryable(exception.status_code)
        and base_details.get("retry_safe") is not False
    )
    failure_kind = _api_failure_kind(exception.status_code, exception.error_type)
    suggested_action = _api_suggested_action(
        status_code=exception.status_code,
        retryable=retryable,
        error_type=exception.error_type,
    )
    if (
        base_details.get("committed") is True
        and base_details.get("retry_safe") is False
    ):
        suggested_action = (
            "The requested change committed. Refresh current state and resolve "
            "the reported runtime failure before taking further action."
        )
    return FailureClassification(
        error_type=exception.error_type,
        failure_kind=failure_kind,
        retryable=retryable,
        phase=phase,
        message=str(exception.detail or ""),
        http_status=exception.status_code,
        retry_after=(
            None
            if base_details.get("retry_after") is None
            else str(base_details["retry_after"])
        ),
        suggested_action=suggested_action,
    )


def _api_status_retryable(status_code: int) -> bool:
    return status_code in {408, 429, 502, 503, 504}


def _api_failure_kind(status_code: int, error_type: str) -> str:
    if status_code == 429:
        return "rate_limited"
    if status_code in {408, 504}:
        return "timeout"
    if status_code in {502, 503}:
        return "service_unavailable"
    if status_code in {401, 403}:
        return "configuration"
    if 400 <= status_code < 500:
        return "bad_request"
    if 500 <= status_code < 600:
        if error_type in {"SystemConfiguration", "SchedulerError"}:
            return "configuration"
        return "server_error"
    return "api_error"


def _api_suggested_action(
    *,
    status_code: int,
    retryable: bool,
    error_type: str,
) -> str:
    if retryable:
        return "Retry with backoff, respecting Retry-After when present."
    if status_code == 404:
        return "Check the referenced id or path before retrying."
    if status_code in {401, 403}:
        return "Check local configuration, credentials, or permissions before retrying."
    if 400 <= status_code < 500:
        return "Change the request parameters before retrying."
    if error_type in {"SystemConfiguration", "SchedulerError"}:
        return "Inspect system configuration and service health before retrying."
    return "Inspect the server-side error details and adjust the request or configuration before retrying."


def _log_api_error_response(
    *,
    error_type: str,
    status_code: int,
    details: dict[str, Any],
) -> None:
    logger.add_sink("validation").warning(
        "api_error_response_created",
        data={
            "event": "api_error_response_created",
            "error_type": error_type,
            "status_code": status_code,
            "phase": details.get("phase"),
            "failure_kind": details.get("failure_kind"),
            "retryable": details.get("retryable"),
        },
    )


def safe_path_join(*paths: str | os.PathLike[str]) -> str:
    """
    Safely join paths and ensure the result is within expected bounds.

    Args:
        *paths: Path components to join

    Returns:
        Safely joined path

    Raises:
        ValueError: If path traversal is detected
    """
    joined = os.path.join(*paths)

    # Normalize the path to resolve any .. or . components
    normalized = os.path.normpath(joined)

    # Check for path traversal attempts
    if ".." in normalized or normalized.startswith("/"):
        if not normalized.startswith("/app/data"):
            raise ValueError(f"Path traversal detected or invalid path: {normalized}")

    return str(normalized)
