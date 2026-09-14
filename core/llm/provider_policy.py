"""Stable provider-shape policy shared by configuration and model construction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

NATIVE_PROVIDER_NAMES = frozenset(
    {"google", "anthropic", "openai", "grok", "mistral", "openrouter"}
)


def provider_requires_custom_base_url(provider_name: str) -> bool:
    """Return whether a provider uses the generic OpenAI-compatible path."""
    return provider_name not in NATIVE_PROVIDER_NAMES


def custom_provider_base_url_available(
    provider_config: Any,
    *,
    get_secret_value: Callable[[str], str | None],
) -> bool:
    """Return whether a custom provider resolves to a complete HTTP(S) URL."""
    return (
        resolve_provider_base_url(
            provider_config,
            get_secret_value=get_secret_value,
        )
        is not None
    )


def provider_has_configured_base_url(provider_config: Any) -> bool:
    """Return whether provider configuration explicitly names a base URL."""
    return _provider_base_url_value(provider_config) is not None


def resolve_provider_base_url(
    provider_config: Any,
    *,
    get_secret_value: Callable[[str], str | None],
) -> str | None:
    """Resolve a provider base URL only when it is a complete HTTP(S) URL."""
    raw_value = _provider_base_url_value(provider_config)
    if raw_value is None:
        return None
    resolved = (get_secret_value(raw_value) or raw_value).strip()
    if any(character.isspace() for character in resolved):
        return None
    try:
        parsed = urlsplit(resolved)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not hostname:
        return None
    return resolved


def require_provider_base_url(
    provider_name: str,
    provider_config: Any,
    *,
    get_secret_value: Callable[[str], str | None],
) -> str:
    """Resolve a required provider URL or raise an actionable configuration error."""
    raw_value = _provider_base_url_value(provider_config)
    resolved = resolve_provider_base_url(
        provider_config,
        get_secret_value=get_secret_value,
    )
    if resolved is not None:
        return resolved
    configured = raw_value or "<missing>"
    raise ValueError(
        f"Provider '{provider_name}' base_url '{configured}' does not resolve to a "
        "complete HTTP(S) URL with a host. Populate the referenced secret or "
        f"configure providers.{provider_name}.base_url as a literal URL."
    )


def _provider_base_url_value(provider_config: Any) -> str | None:
    raw_value = getattr(provider_config, "base_url", None)
    if raw_value is None and isinstance(provider_config, dict):
        raw_value = provider_config.get("base_url")
    if not isinstance(raw_value, str):
        return None
    value = raw_value.strip()
    if not value or value.lower() == "null":
        return None
    return value
