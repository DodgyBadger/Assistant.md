"""
Typed installation settings persistence and helpers.

Provides typed access to the settings database, covering general settings,
models, providers, and tool metadata. Replaces the legacy mappings loader.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, ValidationError, field_validator

from core.connections import ConnectionRequirement
from core.migration_backups import prepare_migration_backup_path
from core.runtime.paths import get_system_root
from core.settings import repository
from core.settings.model_catalog import CatalogUpgrade, upgrade_model_catalog
from core.settings.upgrades import retire_builtin_providers, retire_embedding_models

SETTINGS_TEMPLATE = Path(__file__).parent / "settings.template.yaml"
_SETTINGS_LOCK = RLock()
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
    description: str | None = None
    user_editable: bool = True
    managed_id: str | None = None
    alternate_aliases: list[str] = Field(default_factory=list)

    @field_validator("capabilities", mode="before")
    @classmethod
    def _normalize_capabilities(cls, value: Any) -> list[str]:
        """Normalize capabilities while preserving non-generative aliases."""
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

        if "embedding" in seen:
            raise ValueError("Embedding models are not supported.")
        if "text" not in seen and "decision" not in seen:
            normalized.insert(0, "text")
        return normalized


class SettingsFile(BaseModel):
    """Typed configuration snapshot independent of its persistence format."""

    _revision: int | None = PrivateAttr(default=None)
    _database_root: Path | None = PrivateAttr(default=None)
    _catalog_update: CatalogUpgrade | None = PrivateAttr(default=None)
    _catalog_issues: list[str] = PrivateAttr(default_factory=list)
    _retired_provider_updates: list[str] = PrivateAttr(default_factory=list)

    settings: dict[str, SettingsEntry] = Field(default_factory=dict)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)

    @field_validator("models", mode="before")
    @classmethod
    def _retire_embedding_models(cls, value: Any) -> Any:
        return retire_embedding_models(value) if isinstance(value, dict) else value


def _resolve_system_root() -> Path:
    """
    Determine the active system root, preferring runtime context over defaults.

    Falls back to environment/default constants when a runtime context has not
    been established (e.g. before bootstrap).
    """
    return get_system_root()


_snapshot_cache: dict[Path, SettingsFile] = {}


def get_settings_database_path() -> Path:
    """Return the database owned by the installation settings subsystem."""
    return repository.database_path(_resolve_system_root())


def load_settings() -> SettingsFile:
    """Read a defensive configuration snapshot and reconcile managed entries."""
    with _SETTINGS_LOCK:
        root = _resolve_system_root()
        conn = repository.connect(root)
        initialized_source: str | None = None
        initializing = False
        try:
            cached = _snapshot_cache.get(root)
            if cached is not None and cached._revision == repository.revision(conn):
                return cached.model_copy(deep=True)
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                revision = repository.revision(conn)
                if revision is None:
                    initializing = True
                    legacy_path = root / "settings.yaml"
                    source = legacy_path if legacy_path.exists() else SETTINGS_TEMPLATE
                    raw_data = yaml.safe_load(source.read_text(encoding="utf-8"))
                    if raw_data is None:
                        raw_data = {}
                    if not isinstance(raw_data, dict):
                        raise ValueError(
                            "Legacy settings must contain a top-level mapping."
                        )
                    for section in ("settings", "models", "providers", "tools"):
                        if raw_data.get(section) is None:
                            raw_data[section] = {}
                    # Validate before backing up or marking initialization complete.
                    initial = SettingsFile.model_validate(raw_data)
                    raw_data = initial.model_dump(mode="json")
                    if legacy_path.exists():
                        backup = prepare_migration_backup_path(
                            root, "settings.yaml.bak"
                        )
                        shutil.copyfile(legacy_path, backup)
                        with backup.open("rb") as handle:
                            os.fsync(handle.fileno())
                        initialized_source = "legacy_yaml"
                    else:
                        initialized_source = "defaults"
                    repository.write_records(conn, raw_data)
                    revision = 1
                    conn.execute(
                        "INSERT INTO configuration_state (singleton, revision, source) "
                        "VALUES (1, ?, ?)",
                        (revision, initialized_source),
                    )
                else:
                    raw_data = repository.read_records(conn)
                template = (
                    yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8")) or {}
                )
                upgrade = upgrade_model_catalog(
                    raw_data["models"], template.get("models", {})
                )
                providers = retire_builtin_providers(
                    raw_data["providers"], upgrade.models
                )
                provider_updates = [
                    name
                    for name, entry in raw_data["providers"].items()
                    if providers.get(name) != entry
                ]
                settings = SettingsFile.model_validate(
                    {**raw_data, "models": upgrade.models, "providers": providers}
                )
                if (
                    upgrade.updated
                    or upgrade.renames
                    or upgrade.removed
                    or provider_updates
                ):
                    repository.write_records(conn, settings.model_dump(mode="json"))
                    revision += 1
                    conn.execute(
                        "UPDATE configuration_state SET revision = ? WHERE singleton = 1",
                        (revision,),
                    )
            settings._revision = revision
            settings._database_root = root
            settings._catalog_issues = upgrade.issues
            settings._catalog_update = upgrade
            settings._retired_provider_updates = provider_updates
        except Exception as exc:
            _log_database_event(
                (
                    "settings_database_initialization_failed"
                    if initializing
                    else "settings_database_load_failed"
                ),
                root,
                error_type=type(exc).__name__,
            )
            raise
        finally:
            conn.close()
        _snapshot_cache[root] = settings.model_copy(deep=True)
        _log_catalog_events(settings)
        if initialized_source:
            _log_database_event(
                "settings_database_initialized",
                root,
                source=initialized_source,
                revision=revision,
            )
        return settings.model_copy(deep=True)


def _log_catalog_events(settings: SettingsFile) -> None:
    from core.logger import UnifiedLogger

    logger = UnifiedLogger(tag="models", default_sinks=["activity"])
    if settings._retired_provider_updates:
        logger.info(
            "Retired built-in providers updated",
            data={
                "event": "retired_builtin_providers_updated",
                "status": "completed",
                "providers": settings._retired_provider_updates,
            },
        )
    upgrade = settings._catalog_update
    if upgrade is None:
        return
    if upgrade.issues:
        logger.warning(
            "Managed model update blocked",
            data={
                "event": "managed_models_update_blocked",
                "status": "blocked",
                "reasons": upgrade.issues,
                "issue": "models:catalog_update:"
                + sha256("\n".join(upgrade.issues).encode()).hexdigest()[:16],
            },
        )
    elif upgrade.updated or upgrade.removed or upgrade.renames:
        logger.info(
            "Managed models updated",
            data={
                "event": "managed_models_updated",
                "status": "completed",
                "models": upgrade.updated,
                "renames": upgrade.renames,
                "removed": upgrade.removed,
            },
        )


def _log_database_event(event: str, root: Path, **fields: Any) -> None:
    # Activity-only logging is safe before settings/logfire bootstrap completes.
    from core.logger import UnifiedLogger

    logger = UnifiedLogger(tag="settings", default_sinks=["activity"])
    data = {
        "event": event,
        "status": "failed" if event.endswith("failed") else "completed",
        "database_path": str(repository.database_path(root)),
        **fields,
    }
    if event.endswith("failed"):
        logger.error("Settings database load failed", data=data)
    else:
        logger.info("Settings database initialized", data=data)


def refresh_settings_cache() -> None:
    """Clear packaged defaults; live snapshots are read from the database."""
    with _SETTINGS_LOCK:
        _snapshot_cache.clear()
        _get_template_tools_config.cache_clear()


def save_settings(settings: SettingsFile) -> None:
    """Commit a typed snapshot only if its original revision is still current."""
    with _SETTINGS_LOCK:
        root = _resolve_system_root()
        if settings._revision is None or settings._database_root != root:
            raise ValueError(
                "Settings snapshot is not current for this installation. Reload and retry."
            )
        validated = SettingsFile.model_validate(settings.model_dump(mode="json"))
        settings._revision = repository.save(
            root, validated.model_dump(mode="json"), settings._revision
        )
        _snapshot_cache.pop(root, None)


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
