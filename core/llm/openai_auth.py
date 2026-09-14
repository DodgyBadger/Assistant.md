"""Pure OpenAI auth-mode resolution helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from core.llm.provider_policy import (
    provider_has_configured_base_url,
    resolve_provider_base_url,
)

OPENAI_AUTH_MODE_API_KEY = "api_key"
OPENAI_AUTH_MODE_OAUTH = "oauth"
OPENAI_OAUTH_TOKEN_SECRET = "OPENAI_OAUTH_TOKEN_STATE"
OPENAI_OAUTH_PENDING_SECRET = "OPENAI_OAUTH_PENDING_STATE"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAIAuthResolution:
    """Resolved OpenAI auth decision for availability and runtime construction."""

    configured_auth_mode: str
    effective_auth_mode: str
    oauth_enabled: bool
    oauth_connected: bool
    api_key_name: str | None
    api_key_available: bool
    base_url_available: bool
    fallback_enabled: bool
    fallback_available: bool
    fallback_used: bool
    available: bool
    status: str
    message: str | None = None


def resolve_openai_auth(
    provider_config: Any,
    *,
    oauth_enabled: bool,
    oauth_connected: bool,
    api_key_available: bool,
    base_url_available: bool = False,
    emit_log: bool = True,
) -> OpenAIAuthResolution:
    """Resolve effective OpenAI auth behavior from config and caller state."""

    configured_auth_mode = _provider_string(
        provider_config, "auth_mode", OPENAI_AUTH_MODE_API_KEY
    )
    if configured_auth_mode not in {OPENAI_AUTH_MODE_API_KEY, OPENAI_AUTH_MODE_OAUTH}:
        configured_auth_mode = OPENAI_AUTH_MODE_API_KEY

    api_key_name = _provider_optional_string(provider_config, "api_key")
    fallback_enabled = _provider_bool(
        provider_config, "oauth_api_key_fallback_enabled", False
    )
    fallback_available = api_key_available or base_url_available

    if not oauth_enabled:
        resolution = _api_key_resolution(
            configured_auth_mode=configured_auth_mode,
            oauth_enabled=False,
            oauth_connected=oauth_connected,
            api_key_name=api_key_name,
            api_key_available=api_key_available,
            base_url_available=base_url_available,
            fallback_enabled=fallback_enabled,
            fallback_available=fallback_available,
            fallback_used=False,
            status="oauth_disabled",
            message=_api_key_missing_message(api_key_name, fallback_available),
        )
        _maybe_log_openai_auth_resolution(resolution, emit_log=emit_log)
        return resolution

    if configured_auth_mode == OPENAI_AUTH_MODE_API_KEY:
        if not fallback_available and oauth_connected:
            resolution = OpenAIAuthResolution(
                configured_auth_mode=configured_auth_mode,
                effective_auth_mode=OPENAI_AUTH_MODE_OAUTH,
                oauth_enabled=True,
                oauth_connected=True,
                api_key_name=api_key_name,
                api_key_available=api_key_available,
                base_url_available=base_url_available,
                fallback_enabled=fallback_enabled,
                fallback_available=fallback_available,
                fallback_used=False,
                available=True,
                status="api_key_missing_oauth_connected",
            )
            _maybe_log_openai_auth_resolution(resolution, emit_log=emit_log)
            return resolution

        resolution = _api_key_resolution(
            configured_auth_mode=configured_auth_mode,
            oauth_enabled=True,
            oauth_connected=oauth_connected,
            api_key_name=api_key_name,
            api_key_available=api_key_available,
            base_url_available=base_url_available,
            fallback_enabled=fallback_enabled,
            fallback_available=fallback_available,
            fallback_used=False,
            status="api_key",
            message=_api_key_missing_message(api_key_name, fallback_available),
        )
        _maybe_log_openai_auth_resolution(resolution, emit_log=emit_log)
        return resolution

    if oauth_connected:
        resolution = OpenAIAuthResolution(
            configured_auth_mode=configured_auth_mode,
            effective_auth_mode=OPENAI_AUTH_MODE_OAUTH,
            oauth_enabled=True,
            oauth_connected=True,
            api_key_name=api_key_name,
            api_key_available=api_key_available,
            base_url_available=base_url_available,
            fallback_enabled=fallback_enabled,
            fallback_available=fallback_available,
            fallback_used=False,
            available=True,
            status="oauth_connected",
        )
        _maybe_log_openai_auth_resolution(resolution, emit_log=emit_log)
        return resolution

    if fallback_enabled and fallback_available:
        resolution = OpenAIAuthResolution(
            configured_auth_mode=configured_auth_mode,
            effective_auth_mode=OPENAI_AUTH_MODE_API_KEY,
            oauth_enabled=True,
            oauth_connected=False,
            api_key_name=api_key_name,
            api_key_available=api_key_available,
            base_url_available=base_url_available,
            fallback_enabled=True,
            fallback_available=True,
            fallback_used=True,
            available=True,
            status="oauth_fallback",
        )
        _maybe_log_openai_auth_resolution(resolution, emit_log=emit_log)
        return resolution

    message = (
        "OpenAI OAuth is selected but no connected OAuth account is available. "
        "Reconnect OpenAI OAuth or switch the provider back to API-key auth."
    )
    if fallback_available:
        message += " API-key fallback is configured but not enabled."

    resolution = OpenAIAuthResolution(
        configured_auth_mode=configured_auth_mode,
        effective_auth_mode=OPENAI_AUTH_MODE_OAUTH,
        oauth_enabled=True,
        oauth_connected=False,
        api_key_name=api_key_name,
        api_key_available=api_key_available,
        base_url_available=base_url_available,
        fallback_enabled=fallback_enabled,
        fallback_available=fallback_available,
        fallback_used=False,
        available=False,
        status="oauth_unavailable",
        message=message,
    )
    _maybe_log_openai_auth_resolution(resolution, emit_log=emit_log)
    return resolution


def openai_oauth_enabled_from_settings(general_settings: Mapping[str, Any]) -> bool:
    """Return the configured global OpenAI OAuth enablement flag."""

    entry = general_settings.get("openai_oauth_enabled")
    return bool(getattr(entry, "value", False))


def openai_provider_api_key_available(
    provider_config: Any,
    *,
    secret_has_value: Callable[[str], bool],
) -> bool:
    """Return True when the OpenAI provider api_key points to a populated secret."""

    api_key_name = _provider_optional_string(provider_config, "api_key")
    return bool(api_key_name and secret_has_value(api_key_name))


def openai_provider_base_url_available(
    provider_config: Any,
    *,
    get_secret_value: Callable[[str], str | None],
) -> bool:
    """Return True when base_url resolves to a complete HTTP(S) URL."""
    return (
        resolve_provider_base_url(
            provider_config,
            get_secret_value=get_secret_value,
        )
        is not None
    )


def openai_api_key_base_url_invalid(
    provider_config: Any,
    *,
    effective_auth_mode: str,
    base_url_available: bool,
) -> bool:
    """Return whether API-key mode has an explicit but unusable endpoint."""
    return (
        effective_auth_mode == OPENAI_AUTH_MODE_API_KEY
        and provider_has_configured_base_url(provider_config)
        and not base_url_available
    )


def openai_oauth_token_connected(raw_state: str | None) -> bool:
    """Return True when stored OAuth state contains a non-empty access token."""

    if not raw_state:
        return False
    try:
        token_state = json.loads(raw_state)
    except json.JSONDecodeError:
        return False
    return bool(
        isinstance(token_state, dict)
        and isinstance(token_state.get("access_token"), str)
        and token_state["access_token"].strip()
    )


def _api_key_resolution(
    *,
    configured_auth_mode: str,
    oauth_enabled: bool,
    oauth_connected: bool,
    api_key_name: str | None,
    api_key_available: bool,
    base_url_available: bool,
    fallback_enabled: bool,
    fallback_available: bool,
    fallback_used: bool,
    status: str,
    message: str | None,
) -> OpenAIAuthResolution:
    available = api_key_available or base_url_available
    return OpenAIAuthResolution(
        configured_auth_mode=configured_auth_mode,
        effective_auth_mode=OPENAI_AUTH_MODE_API_KEY,
        oauth_enabled=oauth_enabled,
        oauth_connected=oauth_connected,
        api_key_name=api_key_name,
        api_key_available=api_key_available,
        base_url_available=base_url_available,
        fallback_enabled=fallback_enabled,
        fallback_available=fallback_available,
        fallback_used=fallback_used,
        available=available,
        status=status if available else "api_key_unavailable",
        message=message if not available else None,
    )


def _api_key_missing_message(
    api_key_name: str | None,
    fallback_available: bool,
) -> str | None:
    if fallback_available:
        return None
    if api_key_name:
        return f"Configure {api_key_name}"
    return "Configure OpenAI API-key auth or connect OpenAI OAuth."


def _provider_string(provider_config: Any, key: str, default: str) -> str:
    value = _provider_value(provider_config, key)
    if not isinstance(value, str) or not value.strip():
        return default
    return value.strip()


def _provider_optional_string(provider_config: Any, key: str) -> str | None:
    value = _provider_value(provider_config, key)
    if not isinstance(value, str) or not value.strip() or value.lower() == "null":
        return None
    return value.strip()


def _provider_bool(provider_config: Any, key: str, default: bool) -> bool:
    value = _provider_value(provider_config, key)
    if value is None:
        return default
    return bool(value)


def _provider_value(provider_config: Any, key: str) -> Any:
    if isinstance(provider_config, Mapping):
        return provider_config.get(key)
    return getattr(provider_config, key, None)


def _maybe_log_openai_auth_resolution(
    resolution: OpenAIAuthResolution,
    *,
    emit_log: bool,
) -> None:
    if not emit_log:
        return
    logger.info(
        "OpenAI auth mode resolved",
        extra={
            "openai_auth_resolution": {
                "mode": resolution.effective_auth_mode,
                "configured_mode": resolution.configured_auth_mode,
                "oauth_globally_enabled": resolution.oauth_enabled,
                "has_api_key_fallback": resolution.fallback_available,
                "fallback_enabled": resolution.fallback_enabled,
                "fallback_used": resolution.fallback_used,
                "oauth_connected": resolution.oauth_connected,
                "available": resolution.available,
                "status": resolution.status,
            }
        },
    )
