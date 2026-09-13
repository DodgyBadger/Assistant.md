"""Thin API projections for principal-owned MCP connection management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Literal

import yaml

from core.identity import require_current_execution_authority
from core.logger import UnifiedLogger
from core.mcp import (
    MCPAuthMode,
    MCPConnection,
    MCPConnectionCreate,
    MCPConnectionService,
    MCPConnectionUpdate,
    MCPMutationUnavailableError,
    MCPStdioConfig,
    MCPTransport,
)
from core.mcp.oauth import (
    MCPOAuthCoordinator,
    mcp_oauth_callback_path,
    parse_oauth_completion,
)
from core.runtime.state import get_runtime_context

from ..exceptions import APIException
from ..models import (
    MCPConnectionCreateRequest,
    MCPConnectionImportRequest,
    MCPConnectionInfo,
    MCPConnectionTestResponse,
    MCPConnectionUpdateRequest,
    MCPCredentialUpdateRequest,
    MCPOAuthClientSecretUpdateRequest,
    MCPOAuthCompleteRequest,
    MCPOAuthStartResponse,
    MCPOAuthStatusResponse,
    MCPStdioConfigInfo,
    OperationResult,
)

logger = UnifiedLogger(tag="mcp-connections")

_IMPORT_FIELDS = {
    "name",
    "transport",
    "executable",
    "working_directory",
    "arguments",
    "environment",
    "roots",
    "allowed_tools",
    "enabled",
}


def list_mcp_connections() -> list[MCPConnectionInfo]:
    """List sanitized connections for request authority."""
    return [_to_info(item) for item in _service().list_connections()]


def parse_mcp_connection_import(
    request: MCPConnectionImportRequest,
) -> MCPConnectionCreateRequest:
    """Parse one strict chat-generated advanced-shell stdio configuration."""
    try:
        payload = yaml.safe_load(request.configuration)
    except yaml.YAMLError as exc:
        raise APIException(
            status_code=400,
            error_type="InvalidMCPConnectionImport",
            message="MCP configuration is not valid YAML or JSON.",
        ) from exc
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) for key in payload
    ):
        raise APIException(
            status_code=400,
            error_type="InvalidMCPConnectionImport",
            message="MCP configuration must be an object.",
        )
    unknown = sorted(set(payload) - _IMPORT_FIELDS)
    if unknown:
        raise APIException(
            status_code=400,
            error_type="InvalidMCPConnectionImport",
            message=f"Unknown MCP configuration field(s): {', '.join(unknown)}.",
        )
    if payload.get("transport") != MCPTransport.ADVANCED_SHELL_STDIO.value:
        raise APIException(
            status_code=400,
            error_type="InvalidMCPConnectionImport",
            message="Imported configuration must use advanced_shell_stdio transport.",
        )
    try:
        return MCPConnectionCreateRequest.model_validate(
            {
                "display_name": payload.get("name"),
                "transport": payload.get("transport"),
                "enabled": payload.get("enabled", True),
                "allowed_tools": payload.get("allowed_tools"),
                "auth_mode": "none",
                "stdio": {
                    "executable": payload.get("executable"),
                    "arguments": payload.get("arguments", []),
                    "working_directory": payload.get("working_directory"),
                    "environment": payload.get("environment", {}),
                    "roots": payload.get("roots", []),
                },
            }
        )
    except ValueError as exc:
        raise APIException(
            status_code=400,
            error_type="InvalidMCPConnectionImport",
            message="MCP configuration fields are invalid.",
        ) from exc


def create_mcp_connection(
    request: MCPConnectionCreateRequest,
) -> MCPConnectionInfo:
    """Create a connection without accepting an owner override."""
    with _domain_errors():
        connection = _service().create_connection(
            MCPConnectionCreate(
                display_name=request.display_name,
                url=request.url,
                transport=MCPTransport(request.transport),
                auth_mode=MCPAuthMode(request.auth_mode),
                header_name=request.header_name,
                enabled=request.enabled,
                allow_private_http=request.allow_private_http,
                allowed_tools=(
                    tuple(request.allowed_tools)
                    if request.allowed_tools is not None
                    else None
                ),
                credential=(
                    request.credential.get_secret_value()
                    if request.credential is not None
                    else None
                ),
                oauth_client_id=request.oauth_client_id,
                oauth_client_secret=(
                    request.oauth_client_secret.get_secret_value()
                    if request.oauth_client_secret is not None
                    else None
                ),
                oauth_scopes=(
                    tuple(request.oauth_scopes)
                    if request.oauth_scopes is not None
                    else None
                ),
                stdio=_stdio_domain(request.stdio),
            )
        )
    _log_change("mcp_connection_created", connection)
    return _to_info(connection)


def update_mcp_connection(
    connection_id: str, request: MCPConnectionUpdateRequest
) -> MCPConnectionInfo:
    """Replace mutable connection metadata under request authority."""
    with _domain_errors():
        connection = _service().update_connection(
            connection_id,
            MCPConnectionUpdate(
                display_name=request.display_name,
                url=request.url,
                transport=MCPTransport(request.transport),
                auth_mode=MCPAuthMode(request.auth_mode),
                header_name=request.header_name,
                enabled=request.enabled,
                allow_private_http=request.allow_private_http,
                allowed_tools=(
                    tuple(request.allowed_tools)
                    if request.allowed_tools is not None
                    else None
                ),
                oauth_client_id=request.oauth_client_id,
                oauth_scopes=(
                    tuple(request.oauth_scopes)
                    if request.oauth_scopes is not None
                    else None
                ),
                stdio=_stdio_domain(request.stdio),
            ),
        )
    _log_change("mcp_connection_updated", connection)
    return _to_info(connection)


def set_mcp_credential(
    connection_id: str, request: MCPCredentialUpdateRequest
) -> MCPConnectionInfo:
    """Set a static credential without returning its value."""
    with _domain_errors():
        connection = _service().set_credential(
            connection_id,
            request.credential.get_secret_value(),
        )
    _log_change("mcp_credential_updated", connection)
    return _to_info(connection)


def clear_mcp_credential(connection_id: str) -> MCPConnectionInfo:
    """Clear a static credential under request authority."""
    with _domain_errors():
        connection = _service().clear_credential(connection_id)
    _log_change("mcp_credential_cleared", connection)
    return _to_info(connection)


def set_mcp_oauth_client_secret(
    connection_id: str, request: MCPOAuthClientSecretUpdateRequest
) -> MCPConnectionInfo:
    """Set a pre-registered OAuth client secret without returning its value."""
    with _domain_errors():
        connection = _service().set_oauth_client_secret(
            connection_id,
            request.client_secret.get_secret_value(),
        )
    _log_change("mcp_oauth_client_secret_updated", connection)
    return _to_info(connection)


def delete_mcp_connection(connection_id: str) -> OperationResult:
    """Delete connection metadata and associated static credential."""
    with _domain_errors():
        _service().delete_connection(connection_id)
    logger.info(
        "MCP connection deleted",
        data={"event": "mcp_connection_deleted", "connection_id": connection_id},
    )
    return OperationResult(
        success=True,
        message="Deleted MCP connection.",
        restart_required=False,
    )


async def test_mcp_connection(connection_id: str) -> MCPConnectionTestResponse:
    """Test managed initialization and return sanitized tool discovery."""
    with _domain_errors():
        connection = _service().get_connection(connection_id)
        if connection is None:
            raise LookupError("MCP connection not found.")
        runtime = get_runtime_context()
        if runtime.mcp_manager is None:
            raise APIException(
                status_code=503,
                error_type="MCPRuntimeUnavailable",
                message="MCP runtime connections are unavailable.",
            )
        result = await runtime.mcp_manager.test_connection(
            require_current_execution_authority(),
            connection,
        )
    return MCPConnectionTestResponse(**asdict(result))


async def start_mcp_oauth(
    connection_id: str,
    *,
    redirect_uri: str,
    redirect_source: Literal["configured", "browser_fallback"],
) -> MCPOAuthStartResponse:
    """Start interactive OAuth without launching a browser on the backend."""
    coordinator = _oauth_coordinator()
    with _domain_errors():
        result = await coordinator.start(
            authority=require_current_execution_authority(),
            connection_id=connection_id,
            redirect_uri=redirect_uri,
        )
    logger.info(
        "MCP OAuth authorization started",
        data={
            "event": "mcp_oauth_started",
            "connection_id": connection_id,
            "redirect_source": redirect_source,
        },
    )
    return MCPOAuthStartResponse(**asdict(result), redirect_source=redirect_source)


async def complete_mcp_oauth(
    connection_id: str, request: MCPOAuthCompleteRequest
) -> MCPOAuthStatusResponse:
    """Complete one callback or pasted-redirect OAuth attempt."""
    with _domain_errors():
        code, state = parse_oauth_completion(
            redirect_url=request.redirect_url,
            code=request.code,
            state=request.state,
        )
        result = await _oauth_coordinator().complete(
            authority=require_current_execution_authority(),
            connection_id=connection_id,
            code=code,
            state=state,
        )
    logger.info(
        "MCP OAuth authorization completed",
        data={"event": "mcp_oauth_completed", "connection_id": connection_id},
    )
    return MCPOAuthStatusResponse(**asdict(result))


async def get_mcp_oauth_status(connection_id: str) -> MCPOAuthStatusResponse:
    """Return sanitized OAuth connection state."""
    with _domain_errors():
        result = await _oauth_coordinator().status(
            authority=require_current_execution_authority(),
            connection_id=connection_id,
        )
    return MCPOAuthStatusResponse(**asdict(result))


async def disconnect_mcp_oauth(connection_id: str) -> OperationResult:
    """Clear pending and durable OAuth state for one connection."""
    with _domain_errors():
        await _oauth_coordinator().disconnect(
            authority=require_current_execution_authority(),
            connection_id=connection_id,
        )
    logger.info(
        "MCP OAuth disconnected",
        data={"event": "mcp_oauth_disconnected", "connection_id": connection_id},
    )
    return OperationResult(
        success=True,
        message="MCP OAuth connection cleared.",
        restart_required=False,
    )


def _service() -> MCPConnectionService:
    service = get_runtime_context().mcp_connections
    if service is None:
        raise APIException(
            status_code=503,
            error_type="SecretsLocked",
            message="MCP configuration is unavailable while encrypted secrets are locked.",
        )
    return service


def _oauth_coordinator() -> MCPOAuthCoordinator:
    coordinator = get_runtime_context().mcp_oauth
    if coordinator is None:
        raise APIException(
            status_code=503,
            error_type="MCPRuntimeUnavailable",
            message="MCP OAuth is unavailable while encrypted secrets are locked.",
        )
    return coordinator


def _to_info(connection: MCPConnection) -> MCPConnectionInfo:
    payload = asdict(connection)
    allowed_tools = payload.get("allowed_tools")
    payload["allowed_tools"] = (
        list(allowed_tools) if allowed_tools is not None else None
    )
    oauth_scopes = payload.get("oauth_scopes")
    payload["oauth_scopes"] = list(oauth_scopes) if oauth_scopes is not None else None
    if connection.stdio is not None:
        payload["stdio"] = {
            "executable": connection.stdio.executable,
            "arguments": list(connection.stdio.arguments),
            "working_directory": connection.stdio.working_directory,
            "environment": dict(connection.stdio.environment),
            "roots": list(connection.stdio.roots),
        }
    public_origin = get_runtime_context().config.public_origin
    payload["oauth_redirect_uri"] = (
        public_origin.build_url(mcp_oauth_callback_path(connection.connection_id))
        if public_origin is not None and connection.auth_mode is MCPAuthMode.OAUTH
        else None
    )
    payload["oauth_redirect_source"] = (
        "configured" if public_origin is not None else "browser_fallback"
    )
    return MCPConnectionInfo.model_validate(payload)


def _stdio_domain(value: MCPStdioConfigInfo | None) -> MCPStdioConfig | None:
    if value is None:
        return None
    return MCPStdioConfig(
        executable=value.executable,
        arguments=tuple(value.arguments),
        working_directory=value.working_directory,
        environment=tuple(value.environment.items()),
        roots=tuple(value.roots),
    )


def _log_change(event: str, connection: MCPConnection) -> None:
    logger.info(
        "MCP connection configuration changed",
        data={
            "event": event,
            "connection_id": connection.connection_id,
            "slug": connection.slug,
            "enabled": connection.enabled,
            "allow_private_http": connection.allow_private_http,
            "auth_mode": connection.auth_mode.value,
            "credential_present": connection.credential_present,
            "config_version": connection.config_version,
        },
    )


@contextmanager
def _domain_errors() -> Iterator[None]:
    try:
        yield
    except MCPMutationUnavailableError as exc:
        raise APIException(
            status_code=503,
            error_type="MCPMutationUnavailable",
            message=(
                "MCP configuration was saved, but runtime refresh failed. "
                "Restart Assistant.md, then inspect the saved state."
            ),
            details={"committed": True, "retry_safe": False},
        ) from exc
    except LookupError as exc:
        raise APIException(
            status_code=404,
            error_type="MCPConnectionNotFound",
            message="MCP connection not found.",
        ) from exc
    except ValueError as exc:
        raise APIException(
            status_code=400,
            error_type="InvalidMCPConnection",
            message=str(exc),
        ) from exc
