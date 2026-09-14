"""Stable provider-shape policy shared by configuration and model construction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
    raw_value = getattr(provider_config, "base_url", None)
    if raw_value is None and isinstance(provider_config, dict):
        raw_value = provider_config.get("base_url")
    if not isinstance(raw_value, str) or not raw_value.strip():
        return False
    value = raw_value.strip()
    resolved = (get_secret_value(value) or value).strip().lower()
    return resolved.startswith(("http://", "https://"))
