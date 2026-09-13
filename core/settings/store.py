"""
Settings file loader and helpers.

Provides typed access to `system/settings.yaml`, covering general settings,
models, providers, and tool metadata. Replaces the legacy mappings loader.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from core.connections import ConnectionRequirement
from core.runtime.paths import get_system_root

SETTINGS_TEMPLATE = Path(__file__).parent / "settings.template.yaml"
RETIRED_BUILTIN_TOOL_NAMES = frozenset(
    {
        "file_ops",
        "file_ops_safe",
        "file_ops_unsafe",
        "propose_file_edits",
        "review_create_file",
        "tavily_crawl",
        "tavily_extract",
        "web_search_duckduckgo",
        "web_search_tavily",
    }
)


class SettingsEntry(BaseModel):
    """Single general settings entry."""

    value: Any
    description: str | None = None
    category: str | None = None
    restart_required: bool = False


class ToolConfig(BaseModel):
    """Configuration for a single tool entry."""

    module: str
    description: str | None = None
    requires_secrets: list[str] = Field(default_factory=list)
    user_editable: bool = False
    chat_visible: bool = True
    requires_connection: ConnectionRequirement | None = None

    def required_secret_keys(self) -> list[str]:
        return list(self.requires_secrets)


class ProviderConfig(BaseModel):
    """Configuration for a model provider."""

    api_key: str | None = None
    base_url: str | None = None
    provider: dict[str, Any] | None = None
    auth_mode: Literal["api_key", "oauth"] = "api_key"
    oauth_api_key_fallback_enabled: bool = False
    user_editable: bool = False


class ModelConfig(BaseModel):
    """Configuration for a model mapping."""

    provider: str
    model_string: str
    capabilities: list[str] = Field(default_factory=lambda: ["text"])
    dimensions: int | None = None
    description: str | None = None
    user_editable: bool = True

    @field_validator("capabilities", mode="before")
    @classmethod
    def _normalize_capabilities(cls, value: Any) -> list[str]:
        """Normalize capabilities while preserving embedding-only aliases."""
        if value is None:
            return ["text"]

        if isinstance(value, str):
            raw_items = [value]
        elif isinstance(value, list):
            raw_items = value
        else:
            raw_items = [str(value)]

        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            cap = str(item).strip().lower()
            if not cap or cap in seen:
                continue
            seen.add(cap)
            normalized.append(cap)

        if "text" not in seen and "embedding" not in seen:
            normalized.insert(0, "text")
        return normalized


class SettingsFile(BaseModel):
    """Root schema for settings.yaml content."""

    settings: dict[str, SettingsEntry] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)


def _resolve_system_root() -> Path:
    """
    Determine the active system root, preferring runtime context over defaults.

    Falls back to environment/default constants when a runtime context has not
    been established (e.g. before bootstrap).
    """
    return get_system_root()


def _resolve_settings_path() -> Path:
    """Determine the active settings file path."""
    return _resolve_system_root() / "settings.yaml"


def _ensure_settings_file(target_path: Path) -> None:
    """Ensure the settings file exists at the target path, seeding from template if missing."""
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if target_path.exists():
        return

    if not SETTINGS_TEMPLATE.exists():
        raise FileNotFoundError(
            f"Default settings template missing: {SETTINGS_TEMPLATE}"
        )

    shutil.copyfile(SETTINGS_TEMPLATE, target_path)


@lru_cache(maxsize=1)
def load_settings() -> SettingsFile:
    """
    Load complete settings.yaml configuration with caching.

    Returns:
        SettingsFile model for general settings, models, providers, and tools.
    """
    settings_file = get_active_settings_path()

    with open(settings_file, encoding="utf-8") as handle:
        raw_data = yaml.safe_load(handle) or {}

    for section in ("settings", "models", "providers", "tools"):
        if raw_data.get(section) is None:
            raw_data[section] = {}

    try:
        return SettingsFile.model_validate(raw_data)
    except ValidationError as exc:
        raise ValueError(f"Invalid settings.yaml configuration: {exc}") from exc


def refresh_settings_cache() -> None:
    """Clear the settings cache so future calls reload from disk."""
    load_settings.cache_clear()  # type: ignore[attr-defined]
    _get_template_tools_config.cache_clear()


def save_settings(settings: SettingsFile) -> None:
    """Persist settings configuration to disk using atomic write."""
    path = get_active_settings_path()
    # Settings may contain string-backed enums used by runtime-only availability
    # checks. Dump through Pydantic's JSON mode so the persisted YAML contains
    # their stable string values rather than Python enum objects.
    data = settings.model_dump(mode="json")

    tmp_path = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(tmp_path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False, allow_unicode=False)

    os.replace(tmp_path, path)


def get_active_settings_path() -> Path:
    """Return the active settings file path, ensuring it exists."""
    path = _resolve_settings_path()
    _ensure_settings_file(path)
    return path


def get_general_settings() -> dict[str, SettingsEntry]:
    """Get general settings section."""
    return load_settings().settings


def get_tools_config() -> dict[str, ToolConfig]:
    """Get tools configuration section from settings."""
    configured = load_settings().tools
    return {**_get_template_tools_config(), **configured}


@lru_cache(maxsize=1)
def _get_template_tools_config() -> dict[str, ToolConfig]:
    """Load packaged built-in tools so additions work before settings repair."""
    try:
        template_raw = (
            yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8")) or {}
        )
        return SettingsFile.model_validate(
            {"tools": template_raw.get("tools", {})}
        ).tools
    except (FileNotFoundError, ValidationError, yaml.YAMLError):
        return {}


def get_enabled_tool_names() -> list[str]:
    """Return registered tool names not disabled by app-wide policy."""
    tools = get_tools_config()
    disabled = set(get_disabled_tool_names())
    return [
        name
        for name in tools
        if name not in disabled and name not in RETIRED_BUILTIN_TOOL_NAMES
    ]


def get_disabled_tool_names() -> list[str]:
    """Return configured disabled tool names that still exist in the registry."""
    settings = load_settings()
    tools = get_tools_config()
    entry = settings.settings.get("disabled_tools")
    raw_disabled = getattr(entry, "value", None)
    if entry is None:
        legacy_entry = settings.settings.get("enabled_tools") or settings.settings.get(
            "default_chat_tools"
        )
        legacy_enabled = getattr(legacy_entry, "value", None)
        if legacy_entry is not None:
            allowed = (
                {str(item).strip() for item in legacy_enabled}
                if isinstance(legacy_enabled, list)
                else set()
            )
            return [
                name
                for name in tools
                if name not in allowed and name not in RETIRED_BUILTIN_TOOL_NAMES
            ]
    if not isinstance(raw_disabled, list):
        return []

    disabled: list[str] = []
    seen: set[str] = set()
    for item in raw_disabled:
        name = str(item).strip()
        if (
            not name
            or name in seen
            or name not in tools
            or name in RETIRED_BUILTIN_TOOL_NAMES
        ):
            continue
        seen.add(name)
        disabled.append(name)
    return disabled


def get_enabled_tools_config() -> dict[str, ToolConfig]:
    """Return configured tools filtered by the app-wide enabled tool list."""
    from core.connections import connection_requirement_available

    tools = get_tools_config()
    enabled: dict[str, ToolConfig] = {}
    for name in get_enabled_tool_names():
        config = tools.get(name)
        if config is None:
            continue
        requirement = config.requires_connection
        if requirement is not None and not connection_requirement_available(
            requirement
        ):
            continue
        enabled[name] = config
    return enabled


def get_models_config() -> dict[str, ModelConfig]:
    """Get models configuration section from settings."""
    return load_settings().models


def get_providers_config() -> dict[str, ProviderConfig]:
    """Get providers configuration section from settings."""
    return load_settings().providers
