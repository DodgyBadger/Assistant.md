"""
Configuration editing helpers for settings.yaml.

Provides validated update operations for user-facing configuration surfaces
such as model mappings and custom providers while preserving developer-only
sections (tools, core providers).
"""

import json
from typing import Any, Literal

import yaml

from core.settings.store import (
    SETTINGS_TEMPLATE,
    ModelConfig,
    ProviderConfig,
    SettingsEntry,
    SettingsFile,
    load_settings,
    refresh_settings_cache,
    save_settings,
)

from . import SettingsError, refresh_configuration_status_cache


def _persist_changes(settings_file: SettingsFile) -> None:
    """Persist settings to disk and refresh related caches."""
    save_settings(settings_file)
    refresh_settings_cache()
    refresh_configuration_status_cache()


def list_general_settings() -> dict[str, SettingsEntry]:
    """Return general settings entries."""
    settings_file = load_settings()
    merged = dict(_template_general_settings())
    merged.update(settings_file.settings)
    return merged


def update_general_setting(name: str, raw_value: str) -> SettingsEntry:
    """Update a general setting value while preserving metadata and types."""
    settings_file = load_settings()
    entry = settings_file.settings.get(name)

    if entry is None:
        entry = _template_general_settings().get(name)
    if entry is None:
        raise SettingsError(f"Setting '{name}' does not exist.")

    coerced_value = _coerce_setting_value(raw_value, entry.value)
    _validate_general_setting_value(name, coerced_value, settings_file)
    settings_file.settings[name] = SettingsEntry(
        value=coerced_value,
        description=entry.description,
        category=entry.category,
        restart_required=entry.restart_required,
    )

    _persist_changes(settings_file)
    return settings_file.settings[name]


def _validate_general_setting_value(
    name: str, value: Any, settings_file: SettingsFile
) -> None:
    """Validate safety-sensitive general settings before persistence."""
    if name == "model_stream_retries" and not 0 <= value <= 5:
        raise SettingsError("Model stream retries must be between 0 and 5.")
    if name == "mcp_max_concurrent_advanced_shell_stdio_launches" and not (
        isinstance(value, int) and 1 <= value <= 32
    ):
        raise SettingsError(
            "Managed advanced-shell stdio launch concurrency must be between 1 and 32."
        )

    delay_names = {
        "model_stream_retry_base_delay_seconds",
        "model_stream_retry_max_delay_seconds",
    }
    if name not in delay_names:
        return
    if not 0 <= value <= 300:
        raise SettingsError(
            "Model stream retry delays must be between 0 and 300 seconds."
        )

    settings = _merged_general_settings(settings_file)
    other_name = next(iter(delay_names - {name}))
    other_entry = settings.get(other_name)
    other_value = getattr(other_entry, "value", None)
    if not isinstance(other_value, int | float):
        return
    base_delay = value if name.endswith("base_delay_seconds") else other_value
    max_delay = value if name.endswith("max_delay_seconds") else other_value
    if base_delay > max_delay:
        raise SettingsError(
            "Model stream retry base delay must not exceed the maximum delay."
        )


def _template_general_settings() -> dict[str, SettingsEntry]:
    try:
        raw = yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    settings = raw.get("settings")
    if not isinstance(settings, dict):
        return {}
    result: dict[str, SettingsEntry] = {}
    for key, value in settings.items():
        try:
            result[str(key)] = SettingsEntry.model_validate(value)
        except Exception:
            continue
    return result


def _coerce_setting_value(raw_value: str, current_value: Any) -> Any:
    """Attempt to convert the provided raw string into the original setting type."""
    raw_value = raw_value if raw_value is not None else ""

    if isinstance(current_value, bool):
        normalized = raw_value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
        raise SettingsError("Value must be true or false.")

    if isinstance(current_value, int) and not isinstance(current_value, bool):
        try:
            return int(raw_value.strip())
        except ValueError as exc:
            raise SettingsError("Value must be an integer.") from exc

    if isinstance(current_value, float):
        try:
            return float(raw_value.strip())
        except ValueError as exc:
            raise SettingsError("Value must be a number.") from exc

    if current_value is None:
        return raw_value or None

    if isinstance(current_value, list | dict):
        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise SettingsError("Value must be valid JSON for this setting.") from exc
        if not isinstance(parsed, type(current_value)):
            raise SettingsError(
                "Updated value type does not match existing setting type."
            )
        return parsed

    return raw_value


def _merged_general_settings(
    settings_file: SettingsFile,
) -> dict[str, SettingsEntry]:
    merged = dict(_template_general_settings())
    merged.update(settings_file.settings)
    return merged


def _setting_list_value(settings_file: SettingsFile, name: str) -> list[str]:
    entry = _merged_general_settings(settings_file).get(name)
    raw_value = getattr(entry, "value", []) if entry else []
    if not isinstance(raw_value, list):
        return []
    return [str(item) for item in raw_value]


def _provider_can_be_edited(
    settings_file: SettingsFile,
    name: str,
    existing: ProviderConfig | None,
) -> bool:
    if existing is None:
        return True
    if getattr(existing, "user_editable", False):
        return True
    return name in _setting_list_value(settings_file, "editable_builtin_providers")


def upsert_model_mapping(
    name: str,
    provider: str,
    model_string: str,
    capabilities: list[str] | None = None,
    dimensions: int | None = None,
    description: str | None = None,
) -> ModelConfig:
    """
    Create or update a model mapping entry.

    Args:
        name: Model alias (lowercase string used by workflows)
        provider: Provider name the model depends on
        model_string: Provider-specific model identifier
        capabilities: Optional model capability list
        dimensions: Optional embedding vector dimensions
        description: Optional human-readable description

    Returns:
        Updated ModelConfig instance

    Raises:
        SettingsError: When attempting to modify a non-editable entry or
                       reference an unknown provider.
    """
    settings_file = load_settings()
    existing = settings_file.models.get(name)

    if existing and not getattr(existing, "user_editable", True):
        raise SettingsError(f"Model '{name}' is not user editable.")

    if provider not in settings_file.providers:
        raise SettingsError(f"Provider '{provider}' is not defined in settings.yaml.")

    user_editable = getattr(existing, "user_editable", True) if existing else True
    resolved_capabilities = (
        capabilities
        if capabilities is not None
        else list(getattr(existing, "capabilities", ["text"])) if existing else ["text"]
    )

    settings_file.models[name] = ModelConfig(
        provider=provider,
        model_string=model_string,
        capabilities=resolved_capabilities,
        dimensions=(
            dimensions
            if dimensions is not None
            else getattr(existing, "dimensions", None)
        ),
        description=description,
        user_editable=user_editable,
    )

    _persist_changes(settings_file)
    return settings_file.models[name]


def delete_model_mapping(name: str) -> None:
    """Delete a user-editable model mapping."""
    settings_file = load_settings()
    existing = settings_file.models.get(name)

    if not existing:
        raise SettingsError(f"Model '{name}' does not exist.")

    if not getattr(existing, "user_editable", True):
        raise SettingsError(f"Model '{name}' is not user editable.")

    del settings_file.models[name]
    _persist_changes(settings_file)


def upsert_provider_config(
    name: str,
    api_key: str | None = None,
    base_url: str | None = None,
    auth_mode: Literal["api_key", "oauth"] | None = None,
    oauth_api_key_fallback_enabled: bool | None = None,
) -> ProviderConfig:
    """
    Create or update a provider configuration entry.

    Only providers marked as user_editable (or new providers) may be modified.
    """
    settings_file = load_settings()
    existing = settings_file.providers.get(name)

    if existing and not _provider_can_be_edited(settings_file, name, existing):
        raise SettingsError(f"Provider '{name}' is not user editable.")

    user_editable = getattr(existing, "user_editable", True) if existing else True
    provider_metadata = getattr(existing, "provider", None) if existing else None
    resolved_auth_mode = (
        auth_mode
        if auth_mode is not None
        else getattr(existing, "auth_mode", "api_key") if existing else "api_key"
    )
    resolved_fallback = (
        oauth_api_key_fallback_enabled
        if oauth_api_key_fallback_enabled is not None
        else (
            getattr(existing, "oauth_api_key_fallback_enabled", False)
            if existing
            else False
        )
    )

    settings_file.providers[name] = ProviderConfig(
        api_key=api_key,
        base_url=base_url,
        provider=provider_metadata,
        auth_mode=resolved_auth_mode,
        oauth_api_key_fallback_enabled=resolved_fallback,
        user_editable=user_editable,
    )

    _persist_changes(settings_file)
    return settings_file.providers[name]


def delete_provider_config(name: str) -> None:
    """Delete a user-editable provider configuration if unused."""
    settings_file = load_settings()
    existing = settings_file.providers.get(name)

    if not existing:
        raise SettingsError(f"Provider '{name}' does not exist.")

    if not getattr(existing, "user_editable", False):
        raise SettingsError(f"Provider '{name}' is not user editable.")

    dependent_models = [
        model_name
        for model_name, model_config in settings_file.models.items()
        if getattr(model_config, "provider", None) == name
    ]

    if dependent_models:
        joined = ", ".join(sorted(dependent_models))
        raise SettingsError(
            f"Provider '{name}' cannot be removed while models reference it ({joined})."
        )

    del settings_file.providers[name]
    _persist_changes(settings_file)
