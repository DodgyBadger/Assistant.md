"""Thin API projections for the principal-owned Google connection."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict

from core.connections import (
    GmailPreferences,
    GoogleConnection,
    GoogleConnectionCreate,
    GoogleConnectionUpdate,
)
from core.identity import require_current_execution_authority
from core.integrations.google import GoogleCapability, GoogleOAuthCoordinator
from core.logger import UnifiedLogger
from core.oauth import parse_oauth_completion
from core.runtime.state import get_runtime_context

from ..exceptions import APIException
from ..models import (
    GoogleClientSecretUpdateRequest,
    GoogleConnectionCreateRequest,
    GoogleConnectionResponse,
    GoogleConnectionUpdateRequest,
    GoogleOAuthCompleteRequest,
    GoogleOAuthStartResponse,
    OperationResult,
)

logger = UnifiedLogger(tag="google-connections")
GOOGLE_OAUTH_CALLBACK_PATH = "/api/system/connections/google/oauth/callback"


def list_google_connections() -> list[GoogleConnectionResponse]:
    authority = require_current_execution_authority()
    runtime = get_runtime_context()
    if runtime.google_connection is None:
        raise _secrets_locked()
    return [
        _google_connection_response(connection.connection_id)
        for connection in runtime.built_in_connections.list_google_connections_for_authority(
            authority
        )
    ]


def create_google_connection(
    request: GoogleConnectionCreateRequest,
) -> GoogleConnectionResponse:
    if get_runtime_context().google_connection is None:
        raise _secrets_locked()
    with _domain_errors():
        connection = (
            get_runtime_context().built_in_connections.create_google_connection(
                GoogleConnectionCreate(
                    display_name=request.display_name,
                    client_id=request.client_id,
                    is_default=request.is_default,
                    gmail=GmailPreferences(**request.gmail.model_dump()),
                )
            )
        )
    return _google_connection_response(connection.connection_id)


def get_google_connection() -> GoogleConnectionResponse:
    """Return sanitized configuration and readiness for the request principal."""
    return _google_connection_response(None)


def get_google_connection_by_id(connection_id: str) -> GoogleConnectionResponse:
    if get_runtime_context().google_connection is None:
        raise _secrets_locked()
    with _domain_errors():
        connection = get_runtime_context().built_in_connections.get_google_connection_for_authority(
            require_current_execution_authority(), connection_id
        )
        if connection is None:
            raise LookupError("Google connection not found.")
    return _google_connection_response(connection.connection_id)


def _google_connection_response(
    connection_id: str | None,
) -> GoogleConnectionResponse:
    authority = require_current_execution_authority()
    runtime = get_runtime_context()
    service = runtime.google_connection
    if service is None:
        raise _secrets_locked()
    connection = runtime.built_in_connections.get_google_connection_for_authority(
        authority, connection_id
    )
    status = service.status(authority, connection_id)
    availability = service.capability_availability(
        authority, GoogleCapability.GMAIL_READ, connection_id
    )
    draft_availability = service.capability_availability(
        authority, GoogleCapability.GMAIL_COMPOSE, connection_id
    )
    gmail = connection.gmail if connection is not None else GmailPreferences()
    return GoogleConnectionResponse.model_validate(
        {
            **asdict(status),
            "granted_scopes": list(status.granted_scopes),
            "gmail": asdict(gmail),
            "gmail_available": availability.available,
            "gmail_missing_scopes": list(availability.missing_scopes),
            "gmail_draft_available": (
                gmail.draft_creation_enabled and draft_availability.available
            ),
            "gmail_draft_missing_scopes": list(draft_availability.missing_scopes),
            "oauth_redirect_uri": _oauth_redirect_uri(
                connection_id=status.connection_id, required=False
            ),
        }
    )


def update_google_connection(
    request: GoogleConnectionUpdateRequest,
) -> GoogleConnectionResponse:
    """Persist non-secret Google client metadata and Gmail preferences."""
    authority = require_current_execution_authority()
    runtime = get_runtime_context()
    if runtime.google_connection is None:
        raise _secrets_locked()
    previous = runtime.built_in_connections.get_google_connection_for_authority(
        authority
    )
    update = GoogleConnectionUpdate(
        client_id=request.client_id,
        display_name=request.display_name,
        is_default=request.is_default,
        gmail=GmailPreferences(**request.gmail.model_dump()),
    )
    with _domain_errors():
        if previous is None:
            connection = runtime.built_in_connections.create_google_connection(
                GoogleConnectionCreate(
                    display_name=update.display_name or "Google",
                    client_id=update.client_id,
                    is_default=True,
                    gmail=update.gmail,
                )
            )
        else:
            service = runtime.google_connection
            if service is None:
                raise _secrets_locked()
            connection = service.update_connection(
                authority, previous.connection_id, update
            )
    logger.info(
        "Google connection configuration changed",
        data={
            "event": "google_connection_updated",
            "config_version": connection.config_version,
        },
    )
    return get_google_connection()


def update_google_connection_by_id(
    connection_id: str, request: GoogleConnectionUpdateRequest
) -> GoogleConnectionResponse:
    authority = require_current_execution_authority()
    runtime = get_runtime_context()
    if runtime.google_connection is None:
        raise _secrets_locked()
    previous = runtime.built_in_connections.get_google_connection_for_authority(
        authority, connection_id
    )
    with _domain_errors():
        if previous is None:
            raise LookupError("Google connection not found.")
        service = runtime.google_connection
        if service is None:
            raise _secrets_locked()
        service.update_connection(
            authority,
            connection_id,
            GoogleConnectionUpdate(
                client_id=request.client_id,
                display_name=request.display_name,
                is_default=request.is_default,
                gmail=GmailPreferences(**request.gmail.model_dump()),
            ),
        )
    return _google_connection_response(connection_id)


def set_google_client_secret(
    request: GoogleClientSecretUpdateRequest,
    connection_id: str | None = None,
) -> GoogleConnectionResponse:
    """Persist the write-only Google OAuth client secret."""
    service = get_runtime_context().google_connection
    if service is None:
        raise _secrets_locked()
    with _domain_errors():
        service.set_client_secret(
            require_current_execution_authority(),
            request.client_secret.get_secret_value(),
            connection_id,
        )
    logger.info(
        "Google OAuth client secret updated",
        data={"event": "google_oauth_client_secret_updated"},
    )
    return _google_connection_response(connection_id)


def start_google_oauth(connection_id: str | None = None) -> GoogleOAuthStartResponse:
    """Start authorization for enabled Gmail capabilities."""
    if get_runtime_context().google_connection is None:
        raise _secrets_locked()
    redirect_uri = _oauth_redirect_uri(connection_id=connection_id, required=True)
    if redirect_uri is None:  # pragma: no cover - guarded by required=True
        raise AssertionError("Required Google OAuth redirect URI was not resolved.")
    authority = require_current_execution_authority()
    connection = (
        get_runtime_context().built_in_connections.get_google_connection_for_authority(
            authority, connection_id
        )
    )
    capabilities = _google_oauth_capabilities(connection)
    with _domain_errors():
        result = _oauth_coordinator().start(
            authority=authority,
            redirect_uri=redirect_uri,
            capabilities=capabilities,
            connection_id=connection_id,
        )
    logger.info("Google OAuth started", data={"event": "google_oauth_started"})
    payload = asdict(result)
    payload["requested_scopes"] = list(result.requested_scopes)
    return GoogleOAuthStartResponse.model_validate(payload)


def _google_oauth_capabilities(
    connection: GoogleConnection | None,
) -> tuple[GoogleCapability, ...]:
    """Request only the Gmail capabilities enabled on the persisted connection."""
    capabilities = [GoogleCapability.GMAIL_READ]
    if connection is not None and connection.gmail.draft_creation_enabled:
        capabilities.append(GoogleCapability.GMAIL_COMPOSE)
    return tuple(capabilities)


async def complete_google_oauth(
    request: GoogleOAuthCompleteRequest,
    connection_id: str | None = None,
) -> GoogleConnectionResponse:
    """Complete one callback or pasted-redirect Google OAuth attempt."""
    with _domain_errors():
        code, state = parse_oauth_completion(
            redirect_url=request.redirect_url,
            code=request.code,
            state=request.state,
        )
        await _oauth_coordinator().complete(
            authority=require_current_execution_authority(),
            code=code,
            state=state,
            connection_id=connection_id,
        )
    logger.info("Google OAuth completed", data={"event": "google_oauth_completed"})
    return _google_connection_response(connection_id)


def disconnect_google_oauth(connection_id: str | None = None) -> OperationResult:
    """Clear the connected grant while preserving reusable client configuration."""
    service = get_runtime_context().google_connection
    if service is None:
        raise _secrets_locked()
    authority = require_current_execution_authority()
    if get_runtime_context().google_connection is None:
        raise _secrets_locked()
    with _domain_errors():
        connection = get_runtime_context().built_in_connections.get_google_connection_for_authority(
            authority, connection_id
        )
        if connection is None:
            raise LookupError("Google connection not found.")
        service.clear_token_state(authority, connection.connection_id)
    logger.info(
        "Google OAuth disconnected", data={"event": "google_oauth_disconnected"}
    )
    return OperationResult(
        success=True,
        message="Google account disconnected.",
        restart_required=False,
    )


def delete_google_connection(
    connection_id: str | None = None,
    *,
    replacement_default_id: str | None = None,
) -> OperationResult:
    """Remove Google metadata and all encrypted Google credentials."""
    runtime = get_runtime_context()
    if runtime.google_connection is None:
        raise _secrets_locked()
    service = runtime.google_connection
    if service is None:
        raise _secrets_locked()
    authority = require_current_execution_authority()
    with _domain_errors():
        connection = runtime.built_in_connections.get_google_connection_for_authority(
            authority, connection_id
        )
        if connection is None:
            raise LookupError("Google connection not found.")
        service.delete_connection(
            authority,
            connection.connection_id,
            replacement_default_id=replacement_default_id,
        )
    logger.info(
        "Google connection deleted", data={"event": "google_connection_deleted"}
    )
    return OperationResult(
        success=True,
        message="Google connection removed.",
        restart_required=False,
    )


def _oauth_coordinator() -> GoogleOAuthCoordinator:
    coordinator = get_runtime_context().google_oauth
    if coordinator is None:
        raise _secrets_locked()
    return coordinator


def _oauth_redirect_uri(*, connection_id: str | None, required: bool) -> str | None:
    del connection_id
    public_origin = get_runtime_context().config.public_origin
    if public_origin is None:
        if required:
            raise APIException(
                status_code=409,
                error_type="PublicURLRequired",
                message=(
                    "Set ASSISTANTMD_PUBLIC_URL before starting Google OAuth so the "
                    "registered callback URI is stable."
                ),
            )
        return None
    return public_origin.build_url(GOOGLE_OAUTH_CALLBACK_PATH)


def _secrets_locked() -> APIException:
    return APIException(
        status_code=503,
        error_type="SecretsLocked",
        message="Google connections are unavailable while encrypted secrets are locked.",
    )


@contextmanager
def _domain_errors() -> Iterator[None]:
    try:
        yield
    except APIException:
        raise
    except LookupError as exc:
        raise APIException(
            status_code=404,
            error_type="GoogleConnectionNotFound",
            message="Google connection not found.",
        ) from exc
    except ValueError as exc:
        raise APIException(
            status_code=400,
            error_type="InvalidGoogleConnection",
            message=str(exc),
        ) from exc
