"""
Configuration editing helpers for application settings.

Provides validated update operations for user-facing configuration surfaces
such as model mappings and custom providers while preserving developer-only
sections (tools, core providers).
"""

from typing import Literal

import yaml

from core.settings.editor_schema import (
    build_editors,
    parse_setting_value,
    settings_issues,
    validate_update,
)
from core.settings.model_catalog import upgrade_model_catalog
from core.settings.model_registry import (
    build_model_registry,
    normalize_model_alias,
    preserve_renamed_alias,
)
from core.settings.repository import StaleSettingsError
from core.settings.store import (
    SETTINGS_TEMPLATE,
    ModelConfig,
    ProviderConfig,
    SettingsEntry,
    SettingsFile,
    get_default_settings,
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
    return {
        name: entry
        for name, entry in load_settings().settings.items()
        if name not in RETIRED_SETTINGS
    }


def update_general_setting(name: str, raw_value: str) -> SettingsEntry:
    """Update a general setting value while preserving metadata and types."""
    settings_file = load_settings().model_copy(deep=True)
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
    return get_default_settings()


def restore_general_setting(name: str) -> SettingsEntry:
    """Remove one override only when its effective default is valid."""
    candidate = load_settings()
    defaults = get_default_settings()
    if name not in defaults or name in RETIRED_SETTINGS:
        raise SettingsError(f"Setting '{name}' has no application default.")
    candidate.settings[name] = defaults[name]
    editors = build_editors(candidate, defaults)
    try:
        validate_update(name, defaults[name].value, candidate.settings, editors)
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc
    _persist_changes(candidate)
    return candidate.settings[name]


def reset_managed_configuration(*, expected_revision: int) -> SettingsFile:
    """Restore managed defaults atomically, retaining independently owned entries."""
    candidate = load_settings()
    if candidate._revision != expected_revision:
        raise StaleSettingsError(
            "Settings changed after the pre-reset backup. Reload and retry."
        )
    raw = yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8")) or {}
    defaults = SettingsFile.model_validate(raw)
    catalog = upgrade_model_catalog(
        {
            name: model.model_dump(mode="json")
            for name, model in candidate.models.items()
        },
        raw.get("models", {}),
    )
    if catalog.issues:
        raise SettingsError(" ".join(catalog.issues))
    candidate.models = SettingsFile.model_validate({"models": catalog.models}).models
    catalog_ids = {
        model.managed_id for model in defaults.models.values() if model.managed_id
    }
    candidate.models = {
        name: model
        for name, model in candidate.models.items()
        if model.managed_id in catalog_ids or not model.managed_id
    }
    managed_ids = {
        model.managed_id for model in candidate.models.values() if model.managed_id
    }
    for name, model in defaults.models.items():
        if model.managed_id not in managed_ids:
            if name in candidate.models:
                raise SettingsError(
                    f"Custom model '{name}' conflicts with a managed default. Rename it before resetting."
                )
            candidate.models[name] = model
    for section in ("providers", "tools"):
        active = getattr(candidate, section)
        baseline = getattr(defaults, section)
        # Packaged provider/tool names remain managed even when their fields
        # are editable (for example Ollama). Independently named entries survive.
        preserved = {
            name: entry for name, entry in active.items() if name not in baseline
        }
        setattr(candidate, section, {**baseline, **preserved})
    candidate.settings = get_default_settings()
    try:
        build_model_registry(candidate.models).validate()
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc
    editors = build_editors(candidate, candidate.settings)
    issues = settings_issues(candidate.settings, editors)
    invalid = [
        f"{name}: {issue.message}" for name, items in issues.items() for issue in items
    ]
    if invalid:
        raise SettingsError(" ".join(invalid))
    _persist_changes(candidate)
    return candidate


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
    original_name: str | None = None,
) -> ModelConfig:
    """Atomically edit or rename one entry without losing its accepted names."""
    settings_file = load_settings().model_copy(deep=True)
    source_name = original_name or name
    existing = settings_file.models.get(source_name)
    if original_name and existing is None:
        raise SettingsError(f"Model '{original_name}' does not exist.")
    if source_name != name and name in settings_file.models:
        raise SettingsError(f"Model '{name}' already exists.")
    if normalize_model_alias(name) == "test" and (
        not existing or normalize_model_alias(source_name) != "test"
    ):
        raise SettingsError("The model alias 'test' is reserved for validation.")
    if provider not in settings_file.providers:
        raise SettingsError(
            f"Provider '{provider}' is not defined in application settings."
        )
    if not model_string.strip():
        raise SettingsError("A provider model identifier is required.")
    try:
        candidate = ModelConfig(
            provider=provider,
            model_string=model_string,
            capabilities=(
                capabilities
                if capabilities is not None
                else existing.capabilities if existing else ["text"]
            ),
            description=(
                description
                if description is not None
                else existing.description if existing else None
            ),
            user_editable=existing.user_editable if existing else True,
            managed_id=existing.managed_id if existing else None,
            alternate_aliases=(
                alternate_aliases
                if alternate_aliases is not None
                else list(existing.alternate_aliases) if existing else []
            ),
        )
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc
    if existing and (existing.managed_id or not existing.user_editable):
        if source_name != name or (
            candidate.provider,
            candidate.model_string,
            candidate.capabilities,
        ) != (existing.provider, existing.model_string, existing.capabilities):
            raise SettingsError(
                f"Model '{source_name}' has a managed binding. Duplicate it as custom to change the binding."
            )
    if existing and source_name != name:
        candidate.alternate_aliases = preserve_renamed_alias(
            source_name, name, candidate.alternate_aliases
        )
        del settings_file.models[source_name]
    settings_file.models[name] = candidate
    try:
        build_model_registry(settings_file.models).validate()
    except ValueError as exc:
        raise SettingsError(str(exc)) from exc
    _persist_changes(settings_file)
    return candidate


def delete_model_mapping(name: str) -> None:
    """Delete a user-editable model mapping."""
    settings_file = load_settings().model_copy(deep=True)
    existing = settings_file.models.get(name)

    if not existing:
        raise SettingsError(f"Model '{name}' does not exist.")

    if existing.managed_id or not existing.user_editable:
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
    settings_file = load_settings().model_copy(deep=True)
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
    settings_file = load_settings().model_copy(deep=True)
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
