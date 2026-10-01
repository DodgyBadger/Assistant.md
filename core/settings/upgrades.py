"""Deterministic upgrades applied by the centralized settings repair path."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_WEB_TOOL_RENAMES = {
    "web_search_duckduckgo": "web_search",
    "web_search_tavily": "web_search",
    "tavily_extract": "web_extract",
    "tavily_crawl": "web_crawl",
}

RETIRED_SETTINGS = frozenset(
    {
        "context_reduction_strategy",
        "compaction_keep_recent",
        "compaction_token_threshold",
        "session_map_author_model",
        "session_map_author_thinking",
        "session_map_low_watermark_tokens",
        "session_map_min_retained_groups",
        "live_session_memory_mode",
        "live_session_memory_decision_model",
        "live_session_memory_author_model",
        "live_session_memory_eligibility_turns",
        "live_session_memory_broad_change_threshold",
        "live_session_memory_field_change_threshold",
        "live_session_memory_max_pending_turns",
        "live_session_memory_max_pending_tokens",
        "live_session_memory_task_timeout_seconds",
        "live_session_memory_max_concurrent_tasks",
        "session_map_gate_model",
        "session_map_gate_threshold",
        "session_map_gate_max_input_tokens",
    }
)


def upgrade_settings_mapping(
    active: dict[str, Any], template: dict[str, Any]
) -> dict[str, Any]:
    """Upgrade known settings contracts while preserving custom sections."""
    upgraded = deepcopy(active)
    settings = upgraded.setdefault("settings", {})
    template_settings = template.get("settings", {})
    if not isinstance(settings, dict) or not isinstance(template_settings, dict):
        return upgraded

    _upgrade_compaction_settings(settings, template_settings)

    for retired_setting in RETIRED_SETTINGS:
        settings.pop(retired_setting, None)

    enabled_entry = settings.get("enabled_tools") or settings.get("default_chat_tools")
    enabled_values = _entry_list(enabled_entry)
    if "disabled_tools" not in settings and enabled_entry is not None:
        old_values = list(enabled_values or [])
        rewritten: list[str] = []
        for value in old_values:
            mapped = _WEB_TOOL_RENAMES.get(value, value)
            if mapped not in rewritten:
                rewritten.append(mapped)
        available_names = _available_tool_names(upgraded, template)
        disabled = [name for name in available_names if name not in rewritten]
        settings["disabled_tools"] = _template_entry_with_value(
            template_settings, "disabled_tools", disabled
        )

        has_duckduckgo = "web_search_duckduckgo" in old_values
        has_tavily = "web_search_tavily" in old_values
        if "web_search_strategy" not in settings and has_duckduckgo != has_tavily:
            strategy = "duckduckgo" if has_duckduckgo else "tavily"
            settings["web_search_strategy"] = _template_entry_with_value(
                template_settings, "web_search_strategy", strategy
            )

    settings.pop("enabled_tools", None)
    settings.pop("default_chat_tools", None)

    old_fetch_entry = settings.get("ingestion_url_fetch_backend")
    if "ingestion_url_fetch_strategy" not in settings and old_fetch_entry is not None:
        settings["ingestion_url_fetch_strategy"] = _template_entry_with_value(
            template_settings,
            "ingestion_url_fetch_strategy",
            _entry_value(old_fetch_entry) or "curl",
        )

    return upgraded


def _upgrade_compaction_settings(
    settings: dict[str, Any], template_settings: dict[str, Any]
) -> None:
    """Move pre-namespace compaction settings into the current contract."""
    renames = {
        "context_reduction_strategy": "compaction_strategy",
        "compaction_token_threshold": "compaction_high_watermark_tokens",
        "session_map_author_model": "compaction_author_model",
        "session_map_author_thinking": "compaction_author_thinking",
        "session_map_low_watermark_tokens": "compaction_low_watermark_tokens",
        "session_map_min_retained_groups": "compaction_retained_turns",
    }
    for old_name, new_name in renames.items():
        if new_name in settings:
            continue
        old_entry = settings.get(old_name)
        if old_entry is None:
            continue
        value = _entry_value(old_entry)
        if old_name == "context_reduction_strategy" and value == "stepped_session_map":
            value = "session_map"
        settings[new_name] = _template_entry_with_value(
            template_settings,
            new_name,
            value,
        )


def _available_tool_names(
    active: dict[str, Any], template: dict[str, Any]
) -> list[str]:
    """Return current built-ins plus user-editable custom tools in stable order."""
    names: list[str] = []
    template_tools = template.get("tools")
    if isinstance(template_tools, dict):
        names.extend(str(name) for name in template_tools)
    active_tools = active.get("tools")
    if isinstance(active_tools, dict):
        for name, entry in active_tools.items():
            if (
                isinstance(entry, dict)
                and entry.get("user_editable") is True
                and str(name) not in names
            ):
                names.append(str(name))
    return names


def _entry_list(entry: Any) -> list[str] | None:
    value = _entry_value(entry)
    if not isinstance(value, list):
        return None
    return [str(item).strip() for item in value if str(item).strip()]


def _entry_value(entry: Any) -> Any:
    if isinstance(entry, dict):
        return entry.get("value")
    return None


def _template_entry_with_value(
    template_settings: dict[str, Any], key: str, value: Any
) -> dict[str, Any]:
    raw_entry = template_settings.get(key)
    entry = deepcopy(raw_entry) if isinstance(raw_entry, dict) else {}
    entry["value"] = value
    return entry
