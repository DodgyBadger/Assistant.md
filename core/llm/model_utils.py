"""
Model creation and resolution for user-friendly model selection.

Maps simple model names (e.g., 'sonnet', 'gpt-4o') to provider-specific model objects
and handles environment-based configuration and API key validation.
"""

from threading import Lock
from typing import Any

from core.llm.model_selection import resolve_model_execution_spec
from core.llm.openai_auth import (
    openai_api_key_base_url_invalid,
    openai_oauth_enabled_from_settings,
    openai_provider_api_key_available,
    openai_provider_base_url_available,
    resolve_openai_auth,
)
from core.llm.openai_oauth import get_openai_oauth_status
from core.llm.provider_policy import (
    custom_provider_base_url_available,
    provider_requires_custom_base_url,
)
from core.logger import UnifiedLogger
from core.settings.secrets_store import get_secret_value, load_secrets, secret_has_value
from core.settings.store import (
    get_general_settings,
    get_models_config,
    get_providers_config,
)

# Create module logger
logger = UnifiedLogger(tag="models")

_MODEL_CACHE_LOCK = Lock()
MODEL_MAPPINGS: dict[str, tuple[str, str]] = {}
MODEL_CAPABILITIES: dict[str, set[str]] = {}
PROVIDER_CONFIGS: dict[str, dict[str, Any]] = {}


#######################################################################
## Model Mappings from YAML Configuration
#######################################################################


def _get_model_mappings() -> dict[str, tuple[str, str]]:
    """Get model mappings in the format expected by existing code."""
    models = get_models_config()

    mappings: dict[str, tuple[str, str]] = {}
    for model_name, model_config in models.items():
        mappings[model_name] = (model_config.provider, model_config.model_string)

    return mappings


def _get_model_capabilities() -> dict[str, set[str]]:
    """Get model capabilities with normalized lowercase values."""
    models = get_models_config()
    capabilities_map: dict[str, set[str]] = {}

    for model_name, model_config in models.items():
        capabilities = list(model_config.capabilities or ["text"])
        normalized = {
            str(capability).strip().lower()
            for capability in capabilities
            if str(capability).strip()
        }
        if not normalized:
            normalized = {"text"}
        capabilities_map[model_name.lower().strip()] = normalized

    return capabilities_map


def _get_provider_configs() -> dict[str, dict[str, Any]]:
    """Get full provider configurations from YAML configuration.

    Returns complete provider configs, allowing access to any configured
    key/value pairs (api_key, base_url, etc.) without parser changes.
    """
    providers = get_providers_config()
    config_map: dict[str, dict[str, Any]] = {}

    for name, provider_config in providers.items():
        config_map[name] = provider_config.model_dump()

    return config_map


def _refresh_model_cache_unlocked() -> None:
    """Refresh module-level caches for model/provider mappings."""
    global MODEL_MAPPINGS, MODEL_CAPABILITIES, PROVIDER_CONFIGS
    MODEL_MAPPINGS = _get_model_mappings()
    MODEL_CAPABILITIES = _get_model_capabilities()
    PROVIDER_CONFIGS = _get_provider_configs()


def refresh_model_cache() -> None:
    """Public helper to refresh cached model/provider mappings."""
    with _MODEL_CACHE_LOCK:
        _refresh_model_cache_unlocked()


# Initialise cache on import so that lookups work immediately.
_refresh_model_cache_unlocked()


#######################################################################
## Model Resolution Functions
#######################################################################


def resolve_model(model_name: str) -> tuple[str, str]:
    """
    Resolve user-friendly model name to provider and model string.

    Args:
        model_name: User-friendly model name (case-insensitive)

    Returns:
        Tuple of (provider, model_string)

    Raises:
        ValueError: If model name is not recognized
    """
    # Case-insensitive lookup
    model_key = model_name.lower().strip()

    if model_key not in MODEL_MAPPINGS:
        available_models = ", ".join(sorted(MODEL_MAPPINGS.keys()))
        raise ValueError(
            f"Unknown model '{model_name}'. Available models: {available_models}"
        )

    return MODEL_MAPPINGS[model_key]


def get_provider_config(provider: str) -> dict[str, Any]:
    """Get full configuration for a provider.

    Returns all configured key/value pairs for the provider.
    Allows client code to access any setting without parser changes.

    Args:
        provider: Provider name (e.g., 'google', 'anthropic', 'custom')

    Returns:
        Dictionary with all provider configuration keys
    """
    return PROVIDER_CONFIGS.get(provider, {})


def _openai_oauth_enabled() -> bool:
    return openai_oauth_enabled_from_settings(get_general_settings())


def get_model_capabilities(model_name: str) -> set[str]:
    """Return normalized capability set for a model alias."""
    model_key = model_name.lower().strip()
    if model_key not in MODEL_MAPPINGS:
        available_models = ", ".join(sorted(MODEL_MAPPINGS.keys()))
        raise ValueError(
            f"Unknown model '{model_name}'. Available models: {available_models}"
        )
    return set(MODEL_CAPABILITIES.get(model_key, {"text"}))


def model_supports_capability(model_name: str, capability: str) -> bool:
    """Return True when model alias includes the requested capability."""
    requested = capability.lower().strip()
    if not requested:
        return False
    execution = resolve_model_execution_spec(model_name)
    if execution.mode == "skip" or not execution.base_alias:
        return False
    # Validation-only built-in model alias handled by ModelDirective.
    # It is not in settings model mappings and should be treated as text-only.
    if execution.base_alias == "test":
        return requested == "text"
    return requested in get_model_capabilities(model_name)


def validate_api_keys(model_name: str) -> None:
    """
    Validate that required API key exists for the specified model.

    Args:
        model_name: User-friendly model name

    Raises:
        ValueError: If required API key is missing
    """
    provider, _ = resolve_model(model_name)
    provider_config = get_provider_config(provider)
    if provider == "openai":
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
        invalid_api_key_base_url = openai_api_key_base_url_invalid(
            provider_config,
            effective_auth_mode=resolution.effective_auth_mode,
            base_url_available=resolution.base_url_available,
        )
        if resolution.available and not invalid_api_key_base_url:
            return
        if invalid_api_key_base_url:
            raise ValueError(
                "The OpenAI provider base_url must resolve to a complete HTTP(S) "
                "URL with a host. Populate its referenced secret or configure a "
                "literal URL before retrying."
            )
        raise ValueError(
            resolution.message
            or f"Model '{model_name}' requires usable OpenAI auth configuration."
        )

    if provider_requires_custom_base_url(provider) and not (
        custom_provider_base_url_available(
            provider_config,
            get_secret_value=get_secret_value,
        )
    ):
        raise ValueError(
            f"Model '{model_name}' requires providers.{provider}.base_url to resolve "
            "to a complete HTTP(S) URL. Populate its referenced secret or configure "
            "a literal URL before retrying."
        )

    required_key = provider_config.get("api_key")

    # No API key required (test model, custom endpoints, etc.)
    if required_key is None or required_key == "null":
        return

    if not secret_has_value(required_key):
        # OpenAI-compatible providers can run against local/remote endpoints
        # that don't require authentication when base_url is configured.
        if openai_provider_base_url_available(
            provider_config,
            get_secret_value=get_secret_value,
        ):
            return
        raise ValueError(
            f"Model '{model_name}' requires secret '{required_key}' to be configured. "
            f"Add this value via the Secrets configuration interface before using the {provider} provider."
        )


def get_available_api_keys() -> dict[str, str]:
    """
    Get dictionary of available API keys from environment.

    Returns:
        Dictionary mapping API key names to their values (only for keys that are set)
    """
    secrets = load_secrets()
    available: dict[str, str] = {}

    for provider_config in PROVIDER_CONFIGS.values():
        key_name = provider_config.get("api_key")
        if key_name and key_name != "null":
            value = secrets.get(key_name)
            if value:
                available[key_name] = value

    return available
