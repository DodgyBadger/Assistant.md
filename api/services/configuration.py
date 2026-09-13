"""API services for settings, providers, OAuth, and secrets."""

import json
import re
import shutil
from pathlib import Path
from typing import Any, Literal

import yaml

from core.ingestion.capabilities import get_pdf_ocr_capability
from core.llm.openai_auth import (
    openai_oauth_enabled_from_settings,
    openai_provider_api_key_available,
    openai_provider_base_url_available,
    resolve_openai_auth,
)
from core.llm.openai_oauth import (
    OpenAIOAuthStateError,
    clear_openai_oauth_state,
    complete_openai_oauth,
    complete_openai_oauth_from_redirect,
    get_openai_oauth_status,
    is_openai_oauth_internal_secret,
    poll_openai_oauth_device_code,
    start_openai_oauth_device_code,
)
from core.llm.openai_oauth import (
    start_openai_oauth as start_openai_oauth_attempt,
)
from core.migration_backups import prepare_migration_backup_path
from core.runtime.paths import (
    resolve_bootstrap_data_root,
    resolve_bootstrap_system_root,
    set_bootstrap_roots,
)
from core.runtime.reload_service import reload_configuration
from core.settings import (
    SettingsError,
    get_default_chat_mode,
    validate_settings,
)
from core.settings.config_editor import (
    delete_model_mapping,
    delete_provider_config,
    list_general_settings,
    update_general_setting,
    upsert_model_mapping,
    upsert_provider_config,
)
from core.settings.secrets_store import (
    delete_secret,
    get_secret_value,
    list_secret_entries,
    remove_secret,
    secret_has_value,
    set_secret_value,
)
from core.settings.store import (
    SETTINGS_TEMPLATE,
    ModelConfig,
    ProviderConfig,
    SettingsEntry,
    get_active_settings_path,
    get_disabled_tool_names,
    get_general_settings,
    get_models_config,
    get_providers_config,
    get_tools_config,
)
from core.settings.upgrades import upgrade_settings_mapping

from ..exceptions import InvalidSystemSettingError, SystemConfigurationError
from ..models import (
    IngestionCapabilityInfo,
    MetadataResponse,
    ModelConfigRequest,
    ModelInfo,
    OpenAIOAuthCompleteRequest,
    OpenAIOAuthDeviceCheckResponse,
    OpenAIOAuthDeviceStartResponse,
    OpenAIOAuthStartRequest,
    OpenAIOAuthStartResponse,
    OperationResult,
    ProviderConfigRequest,
    ProviderInfo,
    SecretInfo,
    SecretUpdateRequest,
    SettingInfo,
    SettingUpdateRequest,
    SystemSettingsResponse,
    ToolInfo,
)
from .shared import get_workflow_loader, logger


def _build_settings_response(path: Path) -> SystemSettingsResponse:
    content = path.read_text(encoding="utf-8")
    return SystemSettingsResponse(
        path=str(path), content=content, size_bytes=len(content.encode("utf-8"))
    )


async def get_system_settings() -> SystemSettingsResponse:
    """Return the current settings YAML content."""
    path = get_active_settings_path()
    return _build_settings_response(path)


async def update_system_settings(new_content: str) -> SystemSettingsResponse:
    """Validate and persist updated settings YAML content."""
    path = get_active_settings_path()

    try:
        parsed = yaml.safe_load(new_content) if new_content.strip() else {}
    except yaml.YAMLError as exc:
        raise SystemConfigurationError(f"Invalid settings YAML: {exc}") from exc

    if parsed is None:
        parsed = {}

    if not isinstance(parsed, dict):
        raise SystemConfigurationError(
            "Settings YAML must contain a top-level mapping (dictionary)."
        )

    normalized_content = (
        new_content if new_content.endswith("\n") else new_content + "\n"
    )

    try:
        path.write_text(normalized_content, encoding="utf-8")
    except Exception as exc:
        raise SystemConfigurationError(f"Failed to write settings file: {exc}") from exc

    reload_configuration()
    logger.info(
        "Settings updated",
        data={"settings_path": str(path), "content_size": len(normalized_content)},
    )

    return _build_settings_response(path)


def repair_settings_from_template() -> SystemSettingsResponse:
    """
    Merge missing keys from settings.template.yaml into the active settings file.

    - Creates a .bak backup of system/settings.yaml before writing.
    - Adds missing keys; existing values are preserved.
    - Prunes removed settings and removed non-user-editable models/providers/tools.
    """
    # Ensure bootstrap roots exist for path resolution
    set_bootstrap_roots(resolve_bootstrap_data_root(), resolve_bootstrap_system_root())
    active_path = get_active_settings_path()

    try:
        template_raw = (
            yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8")) or {}
        )
    except FileNotFoundError as exc:
        raise SystemConfigurationError("Template settings file not found.") from exc
    except yaml.YAMLError as exc:
        raise SystemConfigurationError(
            f"Failed to read template settings: {exc}"
        ) from exc

    try:
        active_raw = yaml.safe_load(active_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SystemConfigurationError(
            f"Failed to read active settings: {exc}"
        ) from exc

    if not isinstance(active_raw, dict):
        raise SystemConfigurationError("Active settings file is not a valid mapping.")
    if not isinstance(template_raw, dict):
        raise SystemConfigurationError("Template settings file is not a valid mapping.")

    # Apply centralized contract upgrades before merging template defaults.
    merged = upgrade_settings_mapping(active_raw, template_raw)
    for section in ("settings", "models", "providers", "tools"):
        if merged.get(section) is None or not isinstance(merged.get(section), dict):
            merged[section] = {}

    template_sections: dict[str, dict] = {}
    for section in ("settings", "models", "providers", "tools"):
        section_val = template_raw.get(section)
        template_sections[section] = (
            section_val if isinstance(section_val, dict) else {}
        )

    # Add missing keys from template (non-destructive)
    for section, template_section in template_raw.items():
        if not isinstance(template_section, dict):
            continue
        active_section = merged.get(section)
        if active_section is None or not isinstance(active_section, dict):
            active_section = {}
        for key, value in template_section.items():
            if key not in active_section:
                active_section[key] = value
        merged[section] = active_section

    # Existing settings entries may need newly introduced metadata fields
    # from the template. Preserve active values and only fill absent metadata.
    active_settings = merged.get("settings", {})
    template_settings = template_sections.get("settings", {})
    if isinstance(active_settings, dict) and isinstance(template_settings, dict):
        for key, template_setting in template_settings.items():
            active_setting = active_settings.get(key)
            if isinstance(active_setting, dict) and isinstance(template_setting, dict):
                for metadata_key in ("description", "category", "restart_required"):
                    if metadata_key in template_setting:
                        active_setting.setdefault(
                            metadata_key, template_setting[metadata_key]
                        )

    # Existing core provider entries may need newly introduced non-secret fields
    # from the template. Preserve all active values and only fill absent keys.
    active_providers = merged.get("providers", {})
    template_providers = template_sections.get("providers", {})
    if isinstance(active_providers, dict) and isinstance(template_providers, dict):
        for key, template_provider in template_providers.items():
            active_provider = active_providers.get(key)
            if isinstance(active_provider, dict) and isinstance(
                template_provider, dict
            ):
                for provider_key, provider_value in template_provider.items():
                    active_provider.setdefault(provider_key, provider_value)

    # Prune removed settings (settings are not user-extensible)
    settings_template_keys = set(template_sections["settings"].keys())
    merged["settings"] = {
        key: val
        for key, val in merged["settings"].items()
        if key in settings_template_keys
    }

    def _is_user_editable(entry: Any, default: bool) -> bool:
        if isinstance(entry, dict):
            ue = entry.get("user_editable")
            if isinstance(ue, bool):
                return ue
        return default

    # Prune removed non-editable tools, models, providers while keeping user-editable/custom entries
    def _prune_section(section_name: str, default_user_editable: bool) -> None:
        template_section = template_sections.get(section_name, {})
        active_section = merged.get(section_name, {})
        if not isinstance(active_section, dict):
            merged[section_name] = {}
            return

        for key in list(active_section.keys()):
            if key in template_section:
                continue
            entry = active_section.get(key)
            if _is_user_editable(entry, default_user_editable):
                continue
            active_section.pop(key, None)

        merged[section_name] = active_section

    _prune_section("tools", default_user_editable=False)
    _prune_section("models", default_user_editable=True)
    _prune_section("providers", default_user_editable=False)

    try:
        backup_path = prepare_migration_backup_path(
            active_path.parent, "settings.yaml.bak"
        )
        shutil.copyfile(active_path, backup_path)
    except Exception as exc:
        raise SystemConfigurationError(
            f"Failed to create settings backup: {exc}"
        ) from exc

    try:
        active_path.write_text(
            yaml.safe_dump(merged, sort_keys=False, allow_unicode=False),
            encoding="utf-8",
        )
    except Exception as exc:
        raise SystemConfigurationError(
            f"Failed to write repaired settings: {exc}"
        ) from exc

    reload_configuration()
    return _build_settings_response(active_path)


#######################################################################
## Configuration Editing Helpers
#######################################################################


def _format_setting_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list | dict):
        try:
            return json.dumps(value, separators=(",", ":"))
        except TypeError:
            return str(value)
    return str(value)


def _build_setting_info(key: str, entry: SettingsEntry) -> SettingInfo:
    return SettingInfo(
        key=key,
        value=_format_setting_value(getattr(entry, "value", None)),
        description=getattr(entry, "description", None),
        category=getattr(entry, "category", None),
        restart_required=bool(getattr(entry, "restart_required", False)),
    )


def get_general_settings_config() -> list[SettingInfo]:
    """Return serialized general settings metadata."""
    settings_map = list_general_settings()
    return [_build_setting_info(key, entry) for key, entry in settings_map.items()]


def update_general_setting_value(
    setting_name: str, payload: SettingUpdateRequest
) -> SettingInfo:
    """Persist a general setting update and refresh configuration caches."""
    try:
        updated = update_general_setting(setting_name, payload.value)
    except SettingsError as exc:
        raise InvalidSystemSettingError(setting_name, str(exc)) from exc

    reload_result = reload_configuration(restart_required=updated.restart_required)
    setting_info = _build_setting_info(setting_name, updated)
    setting_info.restart_required = (
        setting_info.restart_required or reload_result.restart_required
    )
    logger.info(
        "General setting updated",
        data={
            "setting_key": setting_name,
            "restart_required": setting_info.restart_required,
        },
    )
    return setting_info


def _build_model_info(
    name: str,
    config: ModelConfig,
    availability: dict[str, bool],
    issue_messages: dict[str, str] | None = None,
) -> ModelInfo:
    status_message = None
    if issue_messages:
        status_message = issue_messages.get(name)

    return ModelInfo(
        name=name,
        provider=config.provider,
        model_string=config.model_string,
        capabilities=list(config.capabilities or ["text"]),
        dimensions=config.dimensions,
        available=availability.get(name, True),
        user_editable=config.user_editable,
        description=config.description,
        status_message=status_message,
    )


def _general_setting_value(name: str, default: Any) -> Any:
    entry = list_general_settings().get(name)
    return getattr(entry, "value", default) if entry is not None else default


def _editable_builtin_providers() -> set[str]:
    value = _general_setting_value("editable_builtin_providers", [])
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def _openai_oauth_enabled() -> bool:
    return openai_oauth_enabled_from_settings(list_general_settings())


def _build_provider_info(
    name: str, config: ProviderConfig, restart_required: bool = False
) -> ProviderInfo:
    raw_api_key = config.api_key
    raw_base_url = config.base_url
    stored_user_editable = config.user_editable
    fallback_enabled = config.oauth_api_key_fallback_enabled

    api_key_env = raw_api_key if raw_api_key else None
    base_url_env = raw_base_url if raw_base_url else None
    user_editable = bool(stored_user_editable) or name in _editable_builtin_providers()

    api_key_has_value = openai_provider_api_key_available(
        config,
        secret_has_value=secret_has_value,
    )
    base_url_has_value = openai_provider_base_url_available(
        config,
        get_secret_value=get_secret_value,
    )

    if name != "openai":
        return ProviderInfo(
            name=name,
            api_key=api_key_env,
            base_url=base_url_env,
            user_editable=user_editable,
            api_key_has_value=api_key_has_value,
            base_url_has_value=base_url_has_value,
            status_message=None,
            configured_auth_mode=None,
            effective_auth_mode=None,
            oauth_enabled=False,
            oauth_status=None,
            oauth_disabled_reason=None,
            oauth_api_key_fallback_enabled=False,
            oauth_api_key_fallback_available=False,
            oauth_account_id=None,
            oauth_expires_at=None,
            oauth_last_refresh_at=None,
            oauth_last_refresh_error=None,
            oauth_pending_expires_at=None,
            oauth_pending_flow=None,
            oauth_device_verification_url=None,
            oauth_device_user_code=None,
            oauth_device_poll_interval_seconds=None,
            restart_required=restart_required,
        )

    oauth_enabled = _openai_oauth_enabled()
    oauth_connection = get_openai_oauth_status()
    resolution = resolve_openai_auth(
        config,
        oauth_enabled=oauth_enabled,
        oauth_connected=oauth_connection.connected,
        api_key_available=api_key_has_value,
        base_url_available=base_url_has_value,
        emit_log=False,
    )
    configured_auth_mode = resolution.configured_auth_mode
    effective_auth_mode = resolution.effective_auth_mode
    oauth_status = "disabled" if not oauth_enabled else oauth_connection.status
    oauth_disabled_reason = "global_setting" if not oauth_enabled else None

    return ProviderInfo(
        name=name,
        api_key=api_key_env,
        base_url=base_url_env,
        user_editable=user_editable,
        api_key_has_value=api_key_has_value,
        base_url_has_value=base_url_has_value,
        status_message=None,
        configured_auth_mode=configured_auth_mode,
        effective_auth_mode=effective_auth_mode,
        oauth_enabled=oauth_enabled,
        oauth_status=oauth_status,
        oauth_disabled_reason=oauth_disabled_reason,
        oauth_api_key_fallback_enabled=fallback_enabled,
        oauth_api_key_fallback_available=resolution.fallback_available,
        oauth_account_id=oauth_connection.account_id,
        oauth_expires_at=oauth_connection.expires_at,
        oauth_last_refresh_at=oauth_connection.last_refresh_at,
        oauth_last_refresh_error=oauth_connection.last_refresh_error,
        oauth_pending_expires_at=oauth_connection.pending_expires_at,
        oauth_pending_flow=oauth_connection.pending_flow,
        oauth_device_verification_url=oauth_connection.device_verification_url,
        oauth_device_user_code=oauth_connection.device_user_code,
        oauth_device_poll_interval_seconds=(
            oauth_connection.device_poll_interval_seconds
        ),
        restart_required=restart_required,
    )


def _derive_secret_name(provider_name: str, suffix: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "_", provider_name).upper().strip("_")
    if not slug:
        slug = "PROVIDER"
    clean_suffix = suffix.upper().lstrip("_")
    return f"{slug}_{clean_suffix}" if clean_suffix else slug


def _normalize_secret_pointer(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", trimmed).upper().strip("_")
    if not normalized:
        raise SystemConfigurationError("Secret names must include letters or numbers.")
    return normalized


def get_configurable_models() -> list[ModelInfo]:
    """Return model configuration entries with availability metadata."""
    config_status = validate_settings()
    models_config = get_models_config()
    issue_messages = {
        issue.name.split(":", 1)[1]: issue.message
        for issue in config_status.issues
        if issue.name.startswith("model:")
    }
    return [
        _build_model_info(
            name, config, config_status.model_availability, issue_messages
        )
        for name, config in models_config.items()
    ]


def upsert_configurable_model(
    model_name: str, payload: ModelConfigRequest
) -> ModelInfo:
    """Create or update a model mapping, enforcing editability rules."""
    try:
        updated = upsert_model_mapping(
            name=model_name,
            provider=payload.provider,
            model_string=payload.model_string,
            capabilities=payload.capabilities,
            dimensions=payload.dimensions,
            description=payload.description,
        )
    except SettingsError as exc:
        raise SystemConfigurationError(str(exc)) from exc

    reload_result = reload_configuration()
    config_status = reload_result.status
    issue_messages = {
        issue.name.split(":", 1)[1]: issue.message
        for issue in config_status.issues
        if issue.name.startswith("model:")
    }

    logger.info(
        "Model alias upserted",
        data={"alias": model_name, "provider": payload.provider},
    )
    return _build_model_info(
        model_name, updated, config_status.model_availability, issue_messages
    )


def delete_configurable_model(model_name: str) -> OperationResult:
    """Remove a model mapping if permitted."""
    try:
        delete_model_mapping(model_name)
    except SettingsError as exc:
        raise SystemConfigurationError(str(exc)) from exc

    reload_result = reload_configuration()
    logger.info("Model alias deleted", data={"alias": model_name})
    return OperationResult(
        success=True,
        message=f"Model '{model_name}' removed.",
        restart_required=reload_result.restart_required,
    )


def get_configurable_providers() -> list[ProviderInfo]:
    """Return provider configurations suitable for user editing."""
    providers_config = get_providers_config()
    return [
        _build_provider_info(name, config) for name, config in providers_config.items()
    ]


def _openai_provider_info(restart_required: bool = False) -> ProviderInfo:
    providers_config = get_providers_config()
    config = providers_config.get("openai")
    if config is None:
        raise SystemConfigurationError("Built-in openai provider is not configured.")
    return _build_provider_info("openai", config, restart_required=restart_required)


def start_openai_oauth_connection(
    payload: OpenAIOAuthStartRequest,
    *,
    default_redirect_uri: str,
) -> OpenAIOAuthStartResponse:
    """Start an OpenAI OAuth connection attempt."""

    if not _openai_oauth_enabled():
        raise SystemConfigurationError("OpenAI OAuth is disabled by global setting.")
    redirect_uri = payload.redirect_uri or default_redirect_uri
    try:
        result = start_openai_oauth_attempt(redirect_uri=redirect_uri)
    except OpenAIOAuthStateError as exc:
        raise SystemConfigurationError(str(exc)) from exc
    logger.info(
        "OpenAI OAuth start created",
        data={"redirect_uri_configured": bool(payload.redirect_uri)},
    )
    return OpenAIOAuthStartResponse(
        auth_url=result.auth_url,
        state=result.state,
        redirect_uri=result.redirect_uri,
        expires_at=result.expires_at,
    )


async def start_openai_oauth_device_connection() -> OpenAIOAuthDeviceStartResponse:
    """Start an OpenAI OAuth device-code connection attempt."""

    if not _openai_oauth_enabled():
        raise SystemConfigurationError("OpenAI OAuth is disabled by global setting.")
    try:
        result = await start_openai_oauth_device_code()
    except OpenAIOAuthStateError as exc:
        raise SystemConfigurationError(str(exc)) from exc
    logger.info(
        "OpenAI OAuth device-code start created",
        data={"poll_interval_seconds": result.poll_interval_seconds},
    )
    return OpenAIOAuthDeviceStartResponse(
        verification_url=result.verification_url,
        user_code=result.user_code,
        expires_at=result.expires_at,
        poll_interval_seconds=result.poll_interval_seconds,
    )


async def check_openai_oauth_device_connection() -> OpenAIOAuthDeviceCheckResponse:
    """Check an OpenAI OAuth device-code connection attempt."""

    if not _openai_oauth_enabled():
        raise SystemConfigurationError("OpenAI OAuth is disabled by global setting.")
    try:
        token_state = await poll_openai_oauth_device_code()
    except OpenAIOAuthStateError as exc:
        raise SystemConfigurationError(str(exc)) from exc

    status = "connected" if token_state is not None else "pending"
    logger.info("OpenAI OAuth device-code checked", data={"status": status})
    return OpenAIOAuthDeviceCheckResponse(
        status=status,
        provider=_openai_provider_info(),
    )


async def complete_openai_oauth_callback(code: str, state: str) -> ProviderInfo:
    """Complete OpenAI OAuth from callback query parameters."""

    if not _openai_oauth_enabled():
        raise SystemConfigurationError("OpenAI OAuth is disabled by global setting.")
    try:
        await complete_openai_oauth(code=code, state=state)
    except OpenAIOAuthStateError as exc:
        raise SystemConfigurationError(str(exc)) from exc
    logger.info("OpenAI OAuth callback completed", data={"manual": False})
    return _openai_provider_info()


async def complete_openai_oauth_manual(
    payload: OpenAIOAuthCompleteRequest,
) -> ProviderInfo:
    """Complete OpenAI OAuth from a pasted redirect URL or code/state pair."""

    if not _openai_oauth_enabled():
        raise SystemConfigurationError("OpenAI OAuth is disabled by global setting.")
    try:
        await complete_openai_oauth_from_redirect(
            redirect_url=payload.redirect_url,
            code=payload.code,
            state=payload.state,
        )
    except OpenAIOAuthStateError as exc:
        raise SystemConfigurationError(str(exc)) from exc
    logger.info("OpenAI OAuth manual completion finished", data={"manual": True})
    return _openai_provider_info()


def disconnect_openai_oauth_connection() -> OperationResult:
    """Clear OpenAI OAuth token and pending state without changing provider mode."""

    clear_openai_oauth_state()
    logger.info("OpenAI OAuth disconnected", data={})
    return OperationResult(
        success=True,
        message="OpenAI OAuth connection cleared.",
        restart_required=False,
    )


def upsert_configurable_provider(
    provider_name: str, payload: ProviderConfigRequest
) -> ProviderInfo:
    """Create or update a provider configuration entry."""
    providers_config = get_providers_config()
    existing_config = providers_config.get(provider_name)

    # Only reference existing secret names; actual secret values are managed via the Secrets form.
    existing_api_key = None
    existing_base_url = None
    existing_auth_mode: Literal["api_key", "oauth"] = "api_key"
    existing_fallback_enabled = False
    if existing_config:
        existing_api_key = existing_config.api_key
        existing_base_url = existing_config.base_url
        existing_auth_mode = existing_config.auth_mode
        existing_fallback_enabled = existing_config.oauth_api_key_fallback_enabled

    fields_set: set[str] = payload.model_fields_set
    openai_auth_fields = {"auth_mode", "oauth_api_key_fallback_enabled"}
    if provider_name != "openai" and fields_set.intersection(openai_auth_fields):
        raise SystemConfigurationError(
            "OpenAI auth metadata can only be configured for the built-in openai provider."
        )

    if "api_key" in fields_set:
        api_key = _normalize_secret_pointer(payload.api_key)
    else:
        api_key = existing_api_key

    if "base_url" in fields_set:
        base_url = _normalize_secret_pointer(payload.base_url)
    else:
        base_url = existing_base_url

    if "auth_mode" in fields_set:
        auth_mode = payload.auth_mode
    else:
        auth_mode = existing_auth_mode

    if "oauth_api_key_fallback_enabled" in fields_set:
        fallback_enabled = bool(payload.oauth_api_key_fallback_enabled)
    else:
        fallback_enabled = existing_fallback_enabled

    try:
        updated = upsert_provider_config(
            name=provider_name,
            api_key=api_key,
            base_url=base_url,
            auth_mode=auth_mode,
            oauth_api_key_fallback_enabled=fallback_enabled,
        )
    except SettingsError as exc:
        raise SystemConfigurationError(str(exc)) from exc

    reload_result = reload_configuration()

    logger.info(
        "Provider upserted",
        data={
            "alias": provider_name,
            "has_api_key": bool(api_key),
            "has_base_url": bool(base_url),
            "auth_mode": auth_mode if provider_name == "openai" else None,
            "oauth_api_key_fallback_enabled": (
                fallback_enabled if provider_name == "openai" else None
            ),
        },
    )
    return _build_provider_info(
        provider_name,
        updated,
        restart_required=reload_result.restart_required,
    )


def delete_configurable_provider(provider_name: str) -> OperationResult:
    """Remove a provider configuration if permitted."""
    try:
        delete_provider_config(provider_name)
    except SettingsError as exc:
        raise SystemConfigurationError(str(exc)) from exc

    reload_result = reload_configuration()
    logger.info("Provider deleted", data={"alias": provider_name})
    return OperationResult(
        success=True,
        message=f"Provider '{provider_name}' removed.",
        restart_required=reload_result.restart_required,
    )


def _collect_known_secret_names() -> set[str]:
    names: set[str] = set()

    providers = get_providers_config()
    for config in providers.values():
        api_key = getattr(config, "api_key", None)
        if api_key and isinstance(api_key, str) and api_key.lower() != "null":
            names.add(api_key)
        base_url = getattr(config, "base_url", None)
        if base_url and isinstance(base_url, str) and "://" not in base_url:
            names.add(base_url)

    tools = get_tools_config()
    for tool in tools.values():
        if hasattr(tool, "required_secret_keys"):
            names.update(tool.required_secret_keys())

    names.add("LOGFIRE_TOKEN")
    return names


def list_secrets() -> list[SecretInfo]:
    entries = list_secret_entries()
    recorded_entries = {
        entry.name: entry
        for entry in entries
        if not is_openai_oauth_internal_secret(entry.name)
    }
    ordered_names: list[str] = [
        entry.name for entry in entries if entry.name in recorded_entries
    ]

    known_names = _collect_known_secret_names()
    seen = set(ordered_names)
    for name in sorted(known_names):
        if name not in seen:
            ordered_names.append(name)
            seen.add(name)

    secrets: list[SecretInfo] = []
    for name in ordered_names:
        entry = recorded_entries.get(name)
        if entry is not None:
            has_value = entry.has_value
            stored = entry.is_overlay
        else:
            has_value = secret_has_value(name)
            stored = False
        secrets.append(SecretInfo(name=name, has_value=has_value, stored=stored))

    return secrets


def update_secret(request: SecretUpdateRequest) -> OperationResult:
    if not request.name:
        raise SystemConfigurationError("Secret name is required.")

    value = (request.value or "").strip()
    if value:
        set_secret_value(request.name, value)
    else:
        remove_secret(request.name)

    reload_result = reload_configuration()

    action = "Updated" if value else "Cleared"
    logger.info(
        "Secret updated",
        data={"name": request.name, "has_value": bool(value)},
    )
    return OperationResult(
        success=True,
        message=f"{action} {request.name}.",
        restart_required=reload_result.restart_required,
    )


def delete_secret_entry(name: str) -> OperationResult:
    if not name:
        raise SystemConfigurationError("Secret name is required.")

    delete_secret(name)
    reload_result = reload_configuration()

    logger.info("Secret deleted", data={"name": name})
    return OperationResult(
        success=True,
        message=f"Deleted {name}.",
        restart_required=reload_result.restart_required,
    )


async def get_metadata() -> MetadataResponse:
    """Return vault, model, tool, and selected settings metadata for the UI."""
    vaults = list(get_workflow_loader().get_vault_info())
    config_status = validate_settings()
    model_issue_messages = {
        issue.name.split(":", 1)[1]: issue.message
        for issue in config_status.issues
        if issue.name.startswith("model:")
    }
    models = [
        _build_model_info(
            name,
            config,
            config_status.model_availability,
            model_issue_messages,
        )
        for name, config in get_models_config().items()
    ]
    tools = [
        ToolInfo(
            name=name,
            description=config.description or "",
            requires_secrets=config.required_secret_keys(),
            available=config_status.tool_availability.get(name, True),
            user_editable=config.user_editable,
            chat_visible=config.chat_visible,
        )
        for name, config in get_tools_config().items()
        if config.chat_visible
    ]
    general_settings = get_general_settings()
    default_entry = general_settings.get("default_context_script")
    default_context_script = (
        str(default_entry.value).strip() or None
        if default_entry is not None and default_entry.value
        else None
    )
    pdf_ocr = get_pdf_ocr_capability()
    return MetadataResponse(
        vaults=vaults,
        models=models,
        tools=tools,
        settings={
            "disabled_tools": get_disabled_tool_names(),
            "default_chat_mode": get_default_chat_mode(),
            "default_model_thinking": getattr(
                general_settings.get("default_model_thinking"), "value", "default"
            ),
            "auto_cache_max_tokens": getattr(
                general_settings.get("auto_cache_max_tokens"), "value", 0
            ),
        },
        ingestion_capabilities={
            "pdf_ocr": IngestionCapabilityInfo(
                available=pdf_ocr.available,
                provider=pdf_ocr.provider,
                missing=list(pdf_ocr.missing),
                features=list(pdf_ocr.features),
                default_order=list(pdf_ocr.default_order),
            )
        },
        default_context_script=default_context_script,
    )
