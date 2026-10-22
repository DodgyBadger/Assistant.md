"""
Configuration editing helpers for settings.yaml.

Provides validated update operations for user-facing configuration surfaces
such as model mappings and custom providers while preserving developer-only
sections (tools, core providers).
"""

from typing import Literal

import yaml

from core.settings.editor_schema import (
    build_editors,
    parse_setting_value,
    validate_update,
)
from core.settings.model_registry import build_model_registry
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
from core.settings.upgrades import RETIRED_SETTINGS

from . import SettingsError, refresh_configuration_status_cache


def _persist_changes(settings_file: SettingsFile) -> None:
    """Persist settings to disk and refresh related caches."""
    save_settings(settings_file)
    refresh_settings_cache()
    refresh_configuration_status_cache()


def list_general_settings() -> dict[str, SettingsEntry]:
    """Return configured values with current product-owned metadata."""
    settings_file = load_settings()
    template_settings = get_template_general_settings()
    merged = dict(settings_file.settings)
    for name, template_entry in template_settings.items():
        configured_entry = settings_file.settings.get(name)
        merged[name] = template_entry.model_copy(
            update={
                "value": (
                    configured_entry.value
                    if configured_entry is not None
                    else template_entry.value
                )
            }
        )
    return {
        name: entry for name, entry in merged.items() if name not in RETIRED_SETTINGS
    }


def update_general_setting(name: str, raw_value: str) -> SettingsEntry:
    """Update a general setting value while preserving metadata and types."""
    settings_file = load_settings()
    if name in RETIRED_SETTINGS:
        raise SettingsError(f"Setting '{name}' does not exist.")
    entry = settings_file.settings.get(name)
    template_entry = get_template_general_settings().get(name)
    if entry is None:
        entry = template_entry
    if entry is None:
        raise SettingsError(f"Setting '{name}' does not exist.")

    templates = get_template_general_settings()
    editors = build_editors(settings_file, templates)
    entries = _merged_general_settings(settings_file)
    try:
        coerced_value = parse_setting_value(name, raw_value, editors[name])
        validate_update(name, coerced_value, entries, editors)
    except (ValueError, OverflowError) as exc:
        raise SettingsError(str(exc)) from exc
    metadata_entry = template_entry or entry
    settings_file.settings[name] = SettingsEntry(
        value=coerced_value,
        description=metadata_entry.description,
        category=metadata_entry.category,
        restart_required=metadata_entry.restart_required,
    )

    _persist_changes(settings_file)
    return settings_file.settings[name]


def get_template_general_settings() -> dict[str, SettingsEntry]:
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


def _merged_general_settings(
    settings_file: SettingsFile,
) -> dict[str, SettingsEntry]:
    merged = dict(get_template_general_settings())
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
    description: str | None = None,
    alternate_aliases: list[str] | None = None,
    display_name: str | None = None,
) -> ModelConfig:
    """
    Create or update a model mapping entry.

    Args:
        name: Model alias (lowercase string used by workflows)
        provider: Provider name the model depends on
        model_string: Provider-specific model identifier
        capabilities: Optional model capability list
        description: Optional human-readable description

    Returns:
        Updated ModelConfig instance

    Raises:
        SettingsError: When attempting to modify a non-editable entry or
                       reference an unknown provider.
    """
    settings_file = load_settings().model_copy(deep=True)
    existing = settings_file.models.get(name)

    if existing and not getattr(existing, "user_editable", True):
        raise SettingsError(f"Model '{name}' is not user editable.")

    if provider not in settings_file.providers:
        raise SettingsError(f"Provider '{provider}' is not defined in settings.yaml.")

    if capabilities and any(
        str(cap).strip().lower() == "embedding" for cap in capabilities
    ):
        raise SettingsError("Embedding models are not supported.")

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
        description=description,
        user_editable=user_editable,
        alternate_aliases=(
            alternate_aliases
            if alternate_aliases is not None
            else list(existing.alternate_aliases) if existing else []
        ),
        display_name=(
            display_name
            if display_name is not None
            else existing.display_name if existing else None
        ),
    )
    try:
        build_model_registry(settings_file.models).validate()
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc

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
