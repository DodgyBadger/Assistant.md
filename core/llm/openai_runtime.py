"""OpenAI provider construction for API-key and OAuth auth modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic_ai.providers.openai import OpenAIProvider

from core.llm.openai_auth import (
    OPENAI_AUTH_MODE_API_KEY,
    OPENAI_AUTH_MODE_OAUTH,
    OpenAIAuthResolution,
    openai_api_key_base_url_invalid,
    openai_oauth_enabled_from_settings,
    openai_provider_api_key_available,
    openai_provider_base_url_available,
    resolve_openai_auth,
)
from core.llm.openai_client import build_openai_sdk_client
from core.llm.openai_oauth import (
    ensure_fresh_openai_oauth_token,
    get_openai_oauth_status,
    load_openai_oauth_token_state,
)
from core.llm.provider_policy import (
    require_provider_base_url,
    resolve_provider_base_url,
)
from core.settings.secrets_store import get_secret_value, secret_has_value
from core.settings.store import get_general_settings


class OpenAIOAuthRuntimeAdapter(Protocol):
    """Adapter boundary for OAuth-backed OpenAI provider construction."""

    def build_provider(
        self,
        *,
        provider_config: dict[str, Any],
        resolution: OpenAIAuthResolution,
        http_client: httpx.AsyncClient,
    ) -> OpenAIProvider:
        """Return a Pydantic AI OpenAI provider for OAuth-backed runtime use."""


OPENAI_CHATGPT_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


@dataclass(frozen=True)
class OpenAIProviderBuildResult:
    """OpenAI provider plus the auth resolution used to build it."""

    provider: OpenAIProvider
    resolution: OpenAIAuthResolution


class DefaultOpenAIOAuthRuntimeAdapter:
    """Build a Pydantic AI OpenAI provider backed by ChatGPT OAuth tokens."""

    def build_provider(
        self,
        *,
        provider_config: dict[str, Any],
        resolution: OpenAIAuthResolution,
        http_client: httpx.AsyncClient,
    ) -> OpenAIProvider:
        """Return an OpenAI provider using the ChatGPT Codex backend."""

        token_state = load_openai_oauth_token_state()
        if token_state is None:
            raise ValueError("OpenAI OAuth is selected but no token is stored.")

        async def bearer_token() -> str:
            refreshed = await ensure_fresh_openai_oauth_token()
            return refreshed.access_token

        headers = {}
        if token_state.account_id:
            headers["ChatGPT-Account-ID"] = token_state.account_id

        client = build_openai_sdk_client(
            api_key=bearer_token,
            base_url=OPENAI_CHATGPT_CODEX_BASE_URL,
            default_headers=headers or None,
            http_client=http_client,
        )
        return OpenAIProvider(openai_client=client)


_oauth_runtime_adapter: OpenAIOAuthRuntimeAdapter | None = None


def set_openai_oauth_runtime_adapter(
    adapter: OpenAIOAuthRuntimeAdapter | None,
) -> None:
    """Set the process-local OAuth runtime adapter."""

    global _oauth_runtime_adapter
    _oauth_runtime_adapter = adapter


def build_openai_provider(
    *,
    provider_config: dict[str, Any],
    http_client: httpx.AsyncClient,
) -> OpenAIProvider:
    """Build an OpenAI provider according to the effective auth mode."""

    return build_openai_provider_with_resolution(
        provider_config=provider_config,
        http_client=http_client,
    ).provider


def build_openai_provider_with_resolution(
    *,
    provider_config: dict[str, Any],
    http_client: httpx.AsyncClient,
) -> OpenAIProviderBuildResult:
    """Build an OpenAI provider and return its auth resolution."""

    api_key = _resolve_config_value(provider_config.get("api_key"))
    base_url = resolve_provider_base_url(
        provider_config,
        get_secret_value=get_secret_value,
    )
    resolution = resolve_openai_auth(
        provider_config,
        oauth_enabled=_openai_oauth_enabled(),
        oauth_connected=get_openai_oauth_status().connected,
        api_key_available=openai_provider_api_key_available(
            provider_config,
            secret_has_value=secret_has_value,
        ),
        base_url_available=openai_provider_base_url_available(
            provider_config,
            get_secret_value=get_secret_value,
        ),
    )

    if resolution.effective_auth_mode == OPENAI_AUTH_MODE_API_KEY:
        if openai_api_key_base_url_invalid(
            provider_config,
            effective_auth_mode=resolution.effective_auth_mode,
            base_url_available=resolution.base_url_available,
        ):
            require_provider_base_url(
                "openai",
                provider_config,
                get_secret_value=get_secret_value,
            )
        client = build_openai_sdk_client(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
        )
        return OpenAIProviderBuildResult(
            provider=OpenAIProvider(openai_client=client),
            resolution=resolution,
        )

    if resolution.effective_auth_mode == OPENAI_AUTH_MODE_OAUTH:
        adapter = _oauth_runtime_adapter or DefaultOpenAIOAuthRuntimeAdapter()
        return OpenAIProviderBuildResult(
            provider=adapter.build_provider(
                provider_config=provider_config,
                resolution=resolution,
                http_client=http_client,
            ),
            resolution=resolution,
        )

    raise ValueError(
        f"Unsupported OpenAI auth mode '{resolution.effective_auth_mode}'."
    )


def _openai_oauth_enabled() -> bool:
    return openai_oauth_enabled_from_settings(get_general_settings())


def _resolve_config_value(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = raw_value.strip()
    if not value or value.lower() == "null":
        return None
    return get_secret_value(value) or value
