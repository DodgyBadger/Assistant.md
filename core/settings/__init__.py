"""
Application settings and configuration health utilities.

Provides a single typed interface for environment-driven settings along with
helpers to diagnose missing configuration required for runtime features.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.llm.openai_auth import (
    OPENAI_OAUTH_TOKEN_SECRET,
    openai_oauth_enabled_from_settings,
    openai_oauth_token_connected,
    openai_provider_api_key_available,
    openai_provider_base_url_available,
    resolve_openai_auth,
)
from core.llm.thinking import ThinkingValue, normalize_thinking_value
from core.settings.secrets_store import get_secret_value, load_secrets, secret_has_value
from core.settings.store import (
    SETTINGS_TEMPLATE,
    ModelConfig,
    ProviderConfig,
    ToolConfig,
    get_general_settings,
    get_models_config,
    get_providers_config,
    get_tools_config,
)
from core.settings.store import (
    get_disabled_tool_names as get_disabled_tool_names,
)
from core.settings.store import (
    get_enabled_tool_names as get_enabled_tool_names,
)
from core.settings.store import (
    get_enabled_tools_config as get_enabled_tools_config,
)


class SettingsError(Exception):
    """Raised when application settings are invalid or unavailable."""


class ConfigurationIssue(BaseModel):
    """Represents a configuration validation issue."""

    name: str
    message: str
    severity: str  # 'error' or 'warning'


class ConfigurationStatus(BaseModel):
    """Aggregated configuration validation results."""

    issues: list[ConfigurationIssue] = Field(default_factory=list)
    tool_availability: dict[str, bool] = Field(default_factory=dict)
    model_availability: dict[str, bool] = Field(default_factory=dict)

    @property
    def errors(self) -> list[ConfigurationIssue]:
        """Return error-severity issues."""
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ConfigurationIssue]:
        """Return warning-severity issues."""
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def is_healthy(self) -> bool:
        """Return True when no error-severity issues exist."""
        return not self.errors

    def add_issue(self, name: str, message: str, severity: str = "error") -> None:
        """Append an issue to the collection."""
        self.issues.append(
            ConfigurationIssue(name=name, message=message, severity=severity)
        )


class AppSettings(BaseSettings):
    """
    Infrastructure settings loaded from environment variables.

    Only infrastructure-level values remain in the environment. Secrets are
    handled separately via the secrets store.
    """

    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", case_sensitive=True
    )

    vaults_root_path: Path | None = Field(default=None, alias="VAULTS_ROOT_PATH")
    public_url: str | None = Field(default=None, alias="ASSISTANTMD_PUBLIC_URL")
    auth_mode: str | None = Field(default=None, alias="ASSISTANTMD_AUTH_MODE")
    auth_secret_file: Path | None = Field(
        default=None, alias="ASSISTANTMD_AUTH_SECRET_FILE"
    )
    auth_secret: SecretStr | None = Field(default=None, alias="ASSISTANTMD_AUTH_SECRET")
    auth_proxy_assertion_header: str = Field(
        default="X-AssistantMD-Proxy-Assertion",
        alias="ASSISTANTMD_AUTH_PROXY_ASSERTION_HEADER",
    )
    auth_trusted_proxy_networks: str | None = Field(
        default=None,
        alias="ASSISTANTMD_AUTH_TRUSTED_PROXY_NETWORKS",
    )
    execution_mode: str | None = Field(default=None, alias="ASSISTANTMD_EXECUTION_MODE")
    compose_profiles: str = Field(default="", alias="COMPOSE_PROFILES")
    shell_host: str = Field(default="advanced-shell", alias="ASSISTANTMD_SHELL_HOST")
    shell_port: int = Field(default=2222, alias="ASSISTANTMD_SHELL_PORT")
    shell_user: str = Field(default="advanced-shell", alias="ASSISTANTMD_SHELL_USER")
    shell_host_key_alias: str | None = Field(
        default=None, alias="ASSISTANTMD_SHELL_HOST_KEY_ALIAS"
    )

    _LLM_SECRET_KEYS = [
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GROK_API_KEY",
        "MISTRAL_API_KEY",
        "DEEPSEEK_API_KEY",
    ]

    @field_validator("vaults_root_path", mode="before")
    @classmethod
    def _expand_vault_path(cls, value: Any) -> Path | None:
        """Expand user paths to absolute Path instances."""
        if value in (None, ""):
            return None
        if isinstance(value, Path):
            return value.expanduser()
        return Path(value).expanduser()

    @field_validator("auth_secret_file", mode="before")
    @classmethod
    def _expand_auth_secret_file(cls, value: Any) -> Path | None:
        """Expand the optional deployment-owned authentication secret path."""
        if value in (None, ""):
            return None
        if isinstance(value, Path):
            return value.expanduser()
        return Path(value).expanduser()

    @field_validator("public_url", mode="before")
    @classmethod
    def _strip_public_url(cls, value: Any) -> str | None:
        """Strip optional deployment input before sanitized runtime validation."""
        if value in (None, ""):
            return None
        return str(value).strip() or None

    def available_llm_keys(self) -> dict[str, str]:
        """
        Return a mapping of LLM API key secret names to values.
        """
        secrets = load_secrets(include_empty=False)
        return {
            key: value for key, value in secrets.items() if key in self._LLM_SECRET_KEYS
        }

    def has_any_llm_key(self) -> bool:
        """Return True when at least one LLM API key is configured."""
        return any(secret_has_value(name) for name in self._LLM_SECRET_KEYS)

    def required_env_keys(self) -> dict[str, str | None]:
        """
        Return a mapping of required environment variable names to their values.

        Used by validation routines to produce targeted warnings.
        """
        required = {
            "VAULTS_ROOT_PATH": (
                str(self.vaults_root_path)
                if self.vaults_root_path is not None
                else None
            )
        }
        return required


@lru_cache(maxsize=1)
def get_app_settings() -> AppSettings:
    """
    Load application settings from environment variables.
    """
    return AppSettings()


def refresh_app_settings_cache() -> None:
    """Clear cached settings so future calls reload from environment."""
    get_app_settings.cache_clear()  # type: ignore[attr-defined]


def validate_settings(
    settings: AppSettings | None = None,
    tools_config: dict[str, ToolConfig] | None = None,
    models_config: dict[str, ModelConfig] | None = None,
    providers_config: dict[str, ProviderConfig] | None = None,
) -> ConfigurationStatus:
    """
    Validate core configuration requirements.

    Args:
        settings: Optional pre-loaded AppSettings instance.

    Returns:
        ConfigurationStatus describing any issues discovered.
    """
    status = ConfigurationStatus()
    app_settings = settings or get_app_settings()
    from core.secrets import get_secrets_bootstrap_status

    secrets_status = get_secrets_bootstrap_status()
    secrets_locked = secrets_status is not None and not secrets_status.ready
    if secrets_locked:
        status.add_issue(
            name="SECRETS_ENCRYPTION_LOCKED",
            message=(
                "Encrypted secrets are locked. Restore or configure the installation "
                "key in .env, then restart Assistant.md. Providers and models are "
                "unavailable; existing secret state has not been changed."
            ),
            severity="warning",
        )
    if app_settings.public_url is None:
        status.add_issue(
            name="PUBLIC_URL_UNSET",
            message=(
                "Canonical public URL is not configured. Local use remains "
                "available, but reverse-proxy deployments should set "
                "ASSISTANTMD_PUBLIC_URL in .env and restart Assistant.md."
            ),
            severity="warning",
        )
    template_sections = _load_template_sections()

    tools = tools_config or get_tools_config()
    for tool_name, tool_config in tools.items():
        required_secrets = []
        if hasattr(tool_config, "required_secret_keys"):
            required_secrets = tool_config.required_secret_keys()
        strategy_name = ""
        try:
            from core.web.config import get_web_tool_strategy_requirements

            strategy_name, strategy_secrets = get_web_tool_strategy_requirements(
                tool_name
            )
            required_secrets = list(
                dict.fromkeys([*required_secrets, *strategy_secrets])
            )
        except Exception as exc:
            status.tool_availability[tool_name] = False
            status.add_issue(
                name=f"tool:{tool_name}",
                message=f"Tool '{tool_name}' has invalid strategy configuration: {exc}",
                severity="warning",
            )
            continue
        missing_secrets = (
            list(required_secrets)
            if secrets_locked
            else [key for key in required_secrets if not secret_has_value(key)]
        )
        status.tool_availability[tool_name] = not missing_secrets
        if missing_secrets:
            strategy_context = (
                f" with strategy '{strategy_name}'" if strategy_name else ""
            )
            status.add_issue(
                name=f"tool:{tool_name}",
                message=(
                    f"Tool '{tool_name}'{strategy_context} unavailable until secrets "
                    f"{missing_secrets} are configured."
                ),
                severity="warning",
            )

    enabled_tool_names = set(get_enabled_tool_names())
    if "browser" in enabled_tool_names:
        from core.runtime.resources import read_cgroup_memory_status

        browser_memory = read_cgroup_memory_status()
        browser_baseline_bytes = 2 * 1024 * 1024 * 1024
        if (
            browser_memory.max_bytes is not None
            and browser_memory.max_bytes < browser_baseline_bytes
        ):
            status.add_issue(
                name="tool:browser:memory",
                message=(
                    "Browser is enabled with less than the supported 2 GB memory "
                    "baseline. Disable browser for the lightweight profile or raise "
                    "the container/host memory limit."
                ),
                severity="warning",
            )

    providers = providers_config or get_providers_config()
    models = models_config or get_models_config()

    for model_name, model_config in models.items():
        provider_name = getattr(model_config, "provider", None) or (
            model_config.get("provider") if isinstance(model_config, dict) else None
        )
        status.model_availability[model_name] = True
        if secrets_locked:
            status.model_availability[model_name] = False
            continue
        if not provider_name:
            continue

        provider_config = providers.get(provider_name)
        if provider_config is None:
            status.model_availability[model_name] = False
            status.add_issue(
                name=f"model:{model_name}",
                message=f"Model '{model_name}' references unknown provider '{provider_name}'.",
            )
            continue

        if provider_name == "openai":
            resolution = resolve_openai_auth(
                provider_config,
                oauth_enabled=openai_oauth_enabled_from_settings(
                    get_general_settings()
                ),
                oauth_connected=openai_oauth_token_connected(
                    get_secret_value(OPENAI_OAUTH_TOKEN_SECRET)
                ),
                api_key_available=openai_provider_api_key_available(
                    provider_config,
                    secret_has_value=secret_has_value,
                ),
                base_url_available=openai_provider_base_url_available(
                    provider_config,
                    get_secret_value=get_secret_value,
                ),
                emit_log=False,
            )
            status.model_availability[model_name] = resolution.available
            if not resolution.available:
                status.add_issue(
                    name=f"model:{model_name}",
                    message=resolution.message or "Configure OpenAI auth.",
                    severity="warning",
                )
            continue

        api_key_name = getattr(provider_config, "api_key", None)
        if (
            isinstance(api_key_name, str)
            and api_key_name.lower() != "null"
            and api_key_name
        ):
            if not secret_has_value(api_key_name):
                if openai_provider_base_url_available(
                    provider_config,
                    get_secret_value=get_secret_value,
                ):
                    continue
                status.model_availability[model_name] = False
                status.add_issue(
                    name=f"model:{model_name}",
                    message=f"Configure {api_key_name}",
                    severity="warning",
                )

    if status.model_availability and not any(status.model_availability.values()):
        status.add_issue(
            name="LLM_PROVIDER_CONFIG",
            message=(
                "Configure at least one model with a usable provider (API key or"
                " local base_url)."
            ),
            severity="warning",
        )

    _add_missing_template_issues(
        status,
        template_sections=template_sections,
        active_sections={
            "settings": set((get_general_settings() or {}).keys()),
            "models": set(models.keys()),
            "providers": set(providers.keys()),
            "tools": set(tools.keys()),
        },
    )
    _add_missing_settings_metadata_issues(status, get_general_settings() or {})

    def _is_user_editable(entry: Any, default: bool) -> bool:
        """Best-effort user_editable check for typed/dict entries."""
        if hasattr(entry, "user_editable"):
            try:
                return bool(entry.user_editable)
            except Exception:
                return default
        if isinstance(entry, dict):
            val = entry.get("user_editable")
            if isinstance(val, bool):
                return val
        return default

    # Settings are not user-extensible; flag extras
    settings_template_keys = template_sections.get("settings", set())
    settings_extra = set((get_general_settings() or {}).keys()) - settings_template_keys
    if settings_extra:
        status.add_issue(
            name="settings:extra",
            message=f"Unknown settings present: {', '.join(sorted(settings_extra))}",
            severity="warning",
        )

    def _warn_extras(
        section_name: str, items: dict[str, Any], default_user_editable: bool
    ) -> None:
        template_keys = template_sections.get(section_name, set())
        for key, entry in items.items():
            if key in template_keys:
                continue
            if _is_user_editable(entry, default_user_editable):
                continue
            status.add_issue(
                name=f"{section_name}:extra",
                message=f"Unknown {section_name.rstrip('s')} '{key}' present; run settings repair to clean up.",
                severity="warning",
            )

    _warn_extras("tools", tools, default_user_editable=False)
    _warn_extras("models", models, default_user_editable=True)
    _warn_extras("providers", providers, default_user_editable=False)

    general_settings = get_general_settings() or {}
    for setting_key in (
        "content_import_max_batch_size",
        "ingestion_ocr_timeout_seconds",
        "ingestion_url_connect_timeout_seconds",
        "ingestion_url_max_response_mb",
        "ingestion_url_read_timeout_seconds",
    ):
        entry = general_settings.get(setting_key)
        if entry is None:
            continue
        try:
            value = int(entry.value)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            status.add_issue(
                name=f"setting:{setting_key}",
                message=f"Setting '{setting_key}' must be a positive integer.",
            )

    return status


@lru_cache(maxsize=1)
def get_configuration_status() -> ConfigurationStatus:
    """Return cached configuration status assessment."""
    return validate_settings()


def refresh_configuration_status_cache() -> None:
    """Clear cached configuration status."""
    get_configuration_status.cache_clear()  # type: ignore[attr-defined]


@lru_cache(maxsize=32)
def _get_template_setting_positive_int(setting_key: str, fallback: int) -> int:
    """Return a positive int default from settings.template.yaml for a setting key."""
    try:
        raw = yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return fallback
    value = raw.get("settings", {}).get(setting_key, {}).get("value")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _load_template_sections() -> dict[str, set]:
    """Load template keys for each section to detect missing entries."""
    try:
        raw = yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    sections: dict[str, set] = {}
    for section in ("settings", "models", "providers", "tools"):
        section_data = raw.get(section)
        if isinstance(section_data, dict):
            sections[section] = set(section_data.keys())
    return sections


def _add_missing_template_issues(
    status: ConfigurationStatus,
    template_sections: dict[str, set],
    active_sections: dict[str, set],
) -> None:
    """
    Add warning issues for keys missing in active settings compared to the template.
    """
    for section, template_keys in template_sections.items():
        if not template_keys:
            continue
        missing = template_keys - active_sections.get(section, set())
        if missing:
            status.add_issue(
                name=f"{section}:missing",
                message=f"Settings missing from template: {', '.join(sorted(missing))}",
                severity="warning",
            )


def _add_missing_settings_metadata_issues(
    status: ConfigurationStatus,
    active_settings: dict[str, Any],
) -> None:
    """Warn when existing settings are missing metadata present in the template."""
    try:
        template_raw = (
            yaml.safe_load(SETTINGS_TEMPLATE.read_text(encoding="utf-8")) or {}
        )
    except (FileNotFoundError, yaml.YAMLError):
        return
    template_settings = template_raw.get("settings")
    if not isinstance(template_settings, dict):
        return

    missing: list[str] = []
    for key, template_entry in template_settings.items():
        if not isinstance(template_entry, dict) or key not in active_settings:
            continue
        active_entry = active_settings.get(key)
        for metadata_key in ("description", "category", "restart_required"):
            if metadata_key not in template_entry:
                continue
            active_value = getattr(active_entry, metadata_key, None)
            if active_value is None:
                missing.append(f"{key}.{metadata_key}")

    if missing:
        status.add_issue(
            name="settings:missing_metadata",
            message=f"Settings metadata missing from template: {', '.join(sorted(missing))}",
            severity="warning",
        )


def _setting_int(value: Any | None) -> int:
    """Convert a non-null setting value to an integer."""
    if value is None:
        raise TypeError("Setting value is missing.")
    return int(value)


def _setting_float(value: Any | None) -> float:
    """Convert a non-null setting value to a float."""
    if value is None:
        raise TypeError("Setting value is missing.")
    return float(value)


def get_default_api_timeout() -> float:
    """Return the configured API timeout, falling back to 120 seconds."""
    entry = get_general_settings().get("default_api_timeout")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        return _setting_float(value)
    except (TypeError, ValueError):
        return 120.0


def get_openrouter_ignored_providers() -> list[str]:
    """Return normalized OpenRouter provider slugs to skip for model calls."""
    entry = get_general_settings().get("openrouter_ignored_providers")
    value = getattr(entry, "value", None) if entry is not None else None
    if value is None:
        return ["azure"]

    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return ["azure"]

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        provider = str(item).strip().lower()
        if not provider or provider in seen:
            continue
        seen.add(provider)
        normalized.append(provider)
    return normalized


def get_workflow_task_timeout_seconds() -> float:
    """Return workflow task timeout seconds, where 0 disables the timeout."""
    entry = get_general_settings().get("workflow_task_timeout_seconds")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        timeout = _setting_float(value)
    except (TypeError, ValueError):
        return 0.0
    return timeout if timeout > 0 else 0.0


def get_max_concurrent_workflows() -> int:
    """Return max concurrent workflows across vaults, where 0 disables the limit."""
    entry = get_general_settings().get("max_concurrent_workflows")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        limit = _setting_int(value)
    except (TypeError, ValueError):
        return 0
    return limit if limit > 0 else 0


def get_mcp_max_concurrent_advanced_shell_stdio_launches() -> int:
    """Return the restart-bound managed stdio launch concurrency limit."""
    entry = get_general_settings().get(
        "mcp_max_concurrent_advanced_shell_stdio_launches"
    )
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        limit = _setting_int(value)
    except (TypeError, ValueError):
        return 4
    return max(1, min(limit, 32))


def get_browser_navigation_timeout_seconds() -> float:
    """Return browser navigation timeout seconds, falling back to 20 seconds."""
    entry = get_general_settings().get("browser_navigation_timeout_seconds")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        timeout = _setting_float(value)
    except (TypeError, ValueError):
        return 20.0
    return timeout if timeout > 0 else 20.0


def get_browser_selector_timeout_seconds() -> float:
    """Return browser selector timeout seconds, falling back to 4 seconds."""
    entry = get_general_settings().get("browser_selector_timeout_seconds")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        timeout = _setting_float(value)
    except (TypeError, ValueError):
        return 4.0
    return timeout if timeout > 0 else 4.0


def get_browser_max_concurrent_sessions() -> int:
    """Return the process-wide Chromium concurrency limit."""
    entry = get_general_settings().get("browser_max_concurrent_sessions")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        limit = _setting_int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(limit, 8))


def get_browser_max_calls_per_turn() -> int:
    """Return the browser-specific execution-task call limit."""
    entry = get_general_settings().get("browser_max_calls_per_turn")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        limit = _setting_int(value)
    except (TypeError, ValueError):
        return 4
    return max(0, limit)


def get_browser_min_memory_headroom_bytes() -> int:
    """Return required free cgroup memory before Chromium launch."""
    entry = get_general_settings().get("browser_min_memory_headroom_mb")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        megabytes = _setting_int(value)
    except (TypeError, ValueError):
        megabytes = 512
    return max(0, megabytes) * 1024 * 1024


def get_default_max_output_tokens() -> int:
    """Return the configured max output tokens, falling back to 0 (provider default)."""
    entry = get_general_settings().get("max_output_tokens")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        return _setting_int(value)
    except (TypeError, ValueError):
        return 0


def get_default_model_thinking() -> ThinkingValue:
    """Return the configured default thinking policy."""
    entry = get_general_settings().get("default_model_thinking")
    value = getattr(entry, "value", None) if entry is not None else None
    return normalize_thinking_value(value, source_name="default_model_thinking")


def get_default_chat_mode() -> str:
    """Return the configured default mode for new chat sessions."""
    entry = get_general_settings().get("default_chat_mode")
    value = str(getattr(entry, "value", "normal") or "normal").strip().lower()
    return "inline_edit" if value == "inline_edit" else "normal"


def get_auto_cache_max_tokens() -> int:
    """Return the configured auto-cache token limit, falling back to 0 (disabled)."""
    entry = get_general_settings().get("auto_cache_max_tokens")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        return _setting_int(value)
    except (TypeError, ValueError):
        return 0


def get_chat_tool_calls_limit() -> int:
    """Return the max tool calls per chat response; 0 disables the limit."""
    entry = get_general_settings().get("chat_tool_calls_limit")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def get_chat_model_requests_limit() -> int:
    """Return the max model requests per chat response; 0 disables the limit."""
    entry = get_general_settings().get("chat_model_requests_limit")
    value = getattr(entry, "value", None) if entry is not None else None
    if value is None:
        return _get_template_setting_positive_int("chat_model_requests_limit", 150)
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return _get_template_setting_positive_int("chat_model_requests_limit", 150)
    return parsed if parsed > 0 else 0


def get_persist_model_reasoning_parts() -> bool:
    """Return whether provider reasoning parts should be stored in chat history."""
    entry = get_general_settings().get("persist_model_reasoning_parts")
    value = getattr(entry, "value", None) if entry is not None else None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int | float):
        return bool(value)
    return False


def get_delegate_tool_calls_limit() -> int:
    """Return the max tool calls per delegate child run; 0 disables the limit."""
    entry = get_general_settings().get("delegate_tool_calls_limit")
    value = getattr(entry, "value", None) if entry is not None else None
    if value is None:
        from core.constants import DELEGATE_DEFAULT_MAX_TOOL_CALLS

        return DELEGATE_DEFAULT_MAX_TOOL_CALLS
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        from core.constants import DELEGATE_DEFAULT_MAX_TOOL_CALLS

        return DELEGATE_DEFAULT_MAX_TOOL_CALLS
    return parsed if parsed > 0 else 0


def get_delegate_model_requests_limit() -> int:
    """Return the max model requests per delegate child run; 0 disables the limit."""
    entry = get_general_settings().get("delegate_model_requests_limit")
    value = getattr(entry, "value", None) if entry is not None else None
    if value is None:
        return _get_template_setting_positive_int("delegate_model_requests_limit", 75)
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return _get_template_setting_positive_int("delegate_model_requests_limit", 75)
    return parsed if parsed > 0 else 0


def get_delegate_repeated_failure_limit() -> int:
    """Return allowed identical structured failures before delegate calls are blocked."""
    entry = get_general_settings().get("delegate_repeated_failure_limit")
    value = getattr(entry, "value", None) if entry is not None else None
    if value is None:
        return _get_template_setting_positive_int("delegate_repeated_failure_limit", 2)
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return _get_template_setting_positive_int("delegate_repeated_failure_limit", 2)
    return parsed if parsed > 0 else 0


def get_delegate_timeout_seconds() -> float:
    """Return delegate child-run timeout seconds; 0 disables the timeout."""
    entry = get_general_settings().get("delegate_timeout_seconds")
    value = getattr(entry, "value", None) if entry is not None else None
    if value is None:
        from core.constants import DELEGATE_DEFAULT_TIMEOUT_SECONDS

        return DELEGATE_DEFAULT_TIMEOUT_SECONDS
    try:
        parsed = _setting_float(value)
    except (TypeError, ValueError):
        from core.constants import DELEGATE_DEFAULT_TIMEOUT_SECONDS

        return DELEGATE_DEFAULT_TIMEOUT_SECONDS
    return parsed if parsed > 0 else 0.0


def get_model_stream_retries() -> int:
    """Return retry attempts after the initial model stream; 0 disables retries."""
    entry = get_general_settings().get("model_stream_retries")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return 1
    return max(0, min(parsed, 5))


def get_model_stream_retry_base_delay_seconds() -> float:
    """Return the initial delay for model-stream retries."""
    entry = get_general_settings().get("model_stream_retry_base_delay_seconds")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        parsed = _setting_float(value)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(parsed, 300.0))


def get_model_stream_retry_max_delay_seconds() -> float:
    """Return the maximum delay between model-stream retry attempts."""
    entry = get_general_settings().get("model_stream_retry_max_delay_seconds")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        parsed = _setting_float(value)
    except (TypeError, ValueError):
        return 10.0
    return max(0.0, min(parsed, 300.0))


def get_compaction_type() -> str:
    """Return the configured chat-history compaction policy."""
    entry = get_general_settings().get("compaction_type")
    value = getattr(entry, "value", None) if entry is not None else None
    normalized = str(value or "auto").strip().lower()
    return normalized if normalized in {"none", "suggested", "auto"} else "auto"


def get_compaction_keep_recent() -> int:
    """Return the target recent message count to preserve when compacting."""
    entry = get_general_settings().get("compaction_keep_recent")
    value = getattr(entry, "value", None) if entry is not None else None
    template_default = _get_template_setting_positive_int("compaction_keep_recent", 8)
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return template_default
    return parsed if parsed > 0 else template_default


def get_compaction_token_threshold() -> int:
    """Return the estimated token threshold for chat-history compaction."""
    entry = get_general_settings().get("compaction_token_threshold")
    value = getattr(entry, "value", None) if entry is not None else None
    template_default = _get_template_setting_positive_int(
        "compaction_token_threshold", 80_000
    )
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return template_default
    return parsed if parsed > 0 else template_default


def get_file_search_timeout_seconds() -> float:
    """Return file search timeout seconds, falling back to 10 seconds."""
    entry = get_general_settings().get("file_search_timeout_seconds")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        timeout = _setting_float(value)
    except (TypeError, ValueError):
        return 10.0
    return timeout if timeout > 0 else 10.0


def get_file_list_max_results() -> int:
    """Return max results for file_read list operations (0 disables cap)."""
    settings = get_general_settings()
    entry = settings.get("file_list_max_results") or settings.get(
        "file_ops_safe_list_max_results"
    )
    value = getattr(entry, "value", None) if entry is not None else None
    template_default = _get_template_setting_positive_int("file_list_max_results", 200)
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return template_default
    return parsed if parsed >= 0 else template_default


def get_debug_enabled() -> bool:
    """Return whether diagnostic debug behavior is enabled."""
    entry = get_general_settings().get("debug")
    value = getattr(entry, "value", None) if entry is not None else None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int | float):
        return bool(value)
    return False


def get_vault_state_enabled() -> bool:
    """Return whether vault-state refresh behavior is enabled."""
    entry = get_general_settings().get("vault_state_enabled")
    value = getattr(entry, "value", None) if entry is not None else None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int | float):
        return bool(value)
    return True


def get_vault_scan_interval_seconds() -> int:
    """Return scheduled vault-state refresh interval seconds; 0 disables it."""
    entry = get_general_settings().get("vault_scan_interval_seconds")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def get_task_rollback_enabled() -> bool:
    """Return whether task failure/cancellation rollback behavior is enabled."""
    entry = get_general_settings().get("task_rollback_enabled")
    value = getattr(entry, "value", None) if entry is not None else None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int | float):
        return bool(value)
    return True


def get_vault_state_excluded_patterns() -> list[str]:
    """Return gitignore-style vault-relative patterns excluded from vault state."""
    entry = get_general_settings().get("vault_state_excluded_patterns")
    value = getattr(entry, "value", None) if entry is not None else None
    if isinstance(value, str):
        raw_items = [line.strip() for line in value.splitlines()]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = [
            ".git/",
            "**/.DS_Store",
            "**/__pycache__/",
            "AssistantMD/Chat_Sessions/",
        ]
    return [item for item in raw_items if item and not item.startswith("#")]


def get_task_mutation_retention_days() -> int:
    """Return days to retain attributed vault mutation rows."""
    entry = get_general_settings().get("task_mutation_retention_days")
    value = getattr(entry, "value", None) if entry is not None else None
    template_default = _get_template_setting_positive_int(
        "task_mutation_retention_days", 365
    )
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return template_default
    return parsed if parsed >= 0 else template_default


def get_task_snapshot_retention_days() -> int:
    """Return days to retain task snapshot metadata and files."""
    entry = get_general_settings().get("task_snapshot_retention_days")
    value = getattr(entry, "value", None) if entry is not None else None
    template_default = _get_template_setting_positive_int(
        "task_snapshot_retention_days", 30
    )
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return template_default
    return parsed if parsed >= 0 else template_default


def get_vault_upload_max_mb_per_file() -> int:
    """Return the configured per-file Vault Explorer upload limit in MB."""
    entry = get_general_settings().get("vault_upload_max_mb_per_file")
    value = getattr(entry, "value", None) if entry is not None else None
    template_default = _get_template_setting_positive_int(
        "vault_upload_max_mb_per_file",
        100,
    )
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return template_default
    return parsed if parsed >= 0 else template_default


def get_vault_upload_max_bytes_per_file() -> int:
    """Return the configured per-file Vault Explorer upload limit in bytes."""
    return get_vault_upload_max_mb_per_file() * 1024 * 1024


def get_chunking_max_images_per_prompt() -> int:
    """Return max image attachments per chunked prompt."""
    entry = get_general_settings().get("chunking_max_images_per_prompt")
    value = getattr(entry, "value", None) if entry is not None else None
    template_default = 20
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return template_default
    return parsed if parsed >= 0 else template_default


def get_chunking_max_image_mb_per_image() -> int:
    """Return max MB allowed for a single image attachment."""
    settings = get_general_settings()
    entry_mb = settings.get("chunking_max_image_mb_per_image")
    value_mb = getattr(entry_mb, "value", None) if entry_mb is not None else None
    template_default = _get_template_setting_positive_int(
        "chunking_max_image_mb_per_image", 5
    )
    try:
        parsed_mb = _setting_int(value_mb)
    except (TypeError, ValueError):
        parsed_mb = template_default
    return parsed_mb if parsed_mb >= 0 else template_default


def get_chunking_max_image_bytes_per_image() -> int:
    """Return max bytes allowed for a single chunked image attachment."""
    settings = get_general_settings()
    entry_mb = settings.get("chunking_max_image_mb_per_image")
    value_mb = getattr(entry_mb, "value", None) if entry_mb is not None else None
    if value_mb is not None:
        return get_chunking_max_image_mb_per_image() * 1024 * 1024

    # Backward-compatible fallback for legacy byte-based setting name.
    entry = settings.get("chunking_max_image_bytes_per_image")
    value = getattr(entry, "value", None) if entry is not None else None
    template_default_bytes = (
        _get_template_setting_positive_int("chunking_max_image_mb_per_image", 5)
        * 1024
        * 1024
    )
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return template_default_bytes
    return parsed if parsed >= 0 else template_default_bytes


def get_chunking_max_image_bytes_total() -> int:
    """Return max total bytes allowed for chunked image attachments in one prompt."""
    settings = get_general_settings()
    entry_mb = settings.get("chunking_max_image_mb_total")
    value_mb = getattr(entry_mb, "value", None) if entry_mb is not None else None
    if value_mb is not None:
        try:
            parsed_mb = _setting_int(value_mb)
        except (TypeError, ValueError):
            parsed_mb = 100
        parsed_mb = parsed_mb if parsed_mb >= 0 else 100
        return parsed_mb * 1024 * 1024

    # Backward-compatible fallback for legacy byte-based setting name.
    entry = settings.get("chunking_max_image_bytes_total")
    value = getattr(entry, "value", None) if entry is not None else None
    try:
        parsed = _setting_int(value)
    except (TypeError, ValueError):
        return 100 * 1024 * 1024
    return parsed if parsed >= 0 else 100 * 1024 * 1024


def get_chunking_allow_remote_images() -> bool:
    """Return whether remote markdown image refs are allowed by chunking policy."""
    entry = get_general_settings().get("chunking_allow_remote_images")
    value = getattr(entry, "value", None) if entry is not None else None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int | float):
        return bool(value)
    return False
