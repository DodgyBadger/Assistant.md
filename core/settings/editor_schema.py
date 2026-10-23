"""Product-owned settings editor constraints and current-value validation."""

from __future__ import annotations

import json
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.llm.thinking import normalize_thinking_value, thinking_value_to_label
from core.settings.model_registry import build_model_registry
from core.settings.store import (
    RETIRED_BUILTIN_TOOL_NAMES,
    SettingsEntry,
    SettingsFile,
    get_tools_config,
)


class SettingChoice(BaseModel):
    value: str
    label: str


class SettingEditor(BaseModel):
    kind: Literal[
        "text",
        "integer",
        "number",
        "boolean",
        "select",
        "multiselect",
        "ordered_list",
        "string_list",
        "json",
    ]
    choices: list[SettingChoice] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: bool = False
    min_items: int = 0
    unique_items: bool = False
    help: str | None = None


class SettingIssue(BaseModel):
    code: str
    message: str


_FIXED_CHOICES = {
    "default_chat_mode": ("normal", "inline_edit"),
    "compaction_type": ("auto", "suggested", "none"),
    "compaction_strategy": ("recovery_card", "session_map"),
    "ingestion_pdf_default_mode": ("markdown", "page_images"),
    "ingestion_url_fetch_strategy": ("curl",),
}
_THINKING_SETTINGS = {"default_model_thinking", "compaction_author_thinking"}
_POSITIVE_SETTINGS = {
    "default_api_timeout",
    "browser_navigation_timeout_seconds",
    "browser_selector_timeout_seconds",
    "file_search_timeout_seconds",
    "compaction_high_watermark_tokens",
    "compaction_low_watermark_tokens",
    "ingestion_ocr_timeout_seconds",
    "ingestion_url_connect_timeout_seconds",
    "ingestion_url_read_timeout_seconds",
    "ingestion_url_max_response_mb",
    "content_import_max_batch_size",
    "ingestion_worker_interval_seconds",
    "ingestion_worker_batch_size",
}
_BOUNDS = {
    "model_stream_retries": (0, 5),
    "model_stream_idle_timeout_seconds": (0, 3600),
    "model_stream_retry_base_delay_seconds": (0, 300),
    "model_stream_retry_max_delay_seconds": (0, 300),
    "max_concurrent_delegates": (0, 32),
    "mcp_max_concurrent_advanced_shell_stdio_launches": (1, 32),
    "browser_max_concurrent_sessions": (1, 8),
    "compaction_retained_turns": (1, 100),
}
_RELATIONSHIPS = (
    (
        "model_stream_retry_base_delay_seconds",
        "model_stream_retry_max_delay_seconds",
        False,
    ),
    ("compaction_low_watermark_tokens", "compaction_high_watermark_tokens", True),
)


def _choices(values: list[str] | tuple[str, ...]) -> list[SettingChoice]:
    return [SettingChoice(value=value, label=value) for value in values]


def _context_choices() -> list[SettingChoice]:
    # This setting is global. Vault-only names are legitimate, but their scope
    # must be visible because they are not available in every vault.
    from core.authoring.template_discovery import discover_vaults, list_templates
    from core.runtime.paths import get_data_root

    system_names = {template.name for template in list_templates(None)}
    scopes: dict[str, list[str]] = {}
    for vault in discover_vaults():
        for template in list_templates(get_data_root() / vault):
            if template.source == "vault" and template.name not in system_names:
                scopes.setdefault(template.name, []).append(vault)
    return [
        SettingChoice(
            value=name,
            label=(
                name
                if name in system_names
                else f"{name} (vault: {', '.join(sorted(set(scopes[name])))})"
            ),
        )
        for name in sorted(system_names | scopes.keys())
    ]


def build_editors(
    settings_file: SettingsFile, templates: dict[str, SettingsEntry]
) -> dict[str, SettingEditor]:
    """Build editor metadata from product defaults and current registries."""
    from core.web.registry import WebCapability, web_strategy_registry
    from core.web.strategies import register_builtin_web_strategies

    register_builtin_web_strategies(web_strategy_registry)
    web_choices: dict[str, WebCapability] = {
        "web_search_strategy": "web_search",
        "web_extract_strategy": "web_extract",
        "web_crawl_strategy": "web_crawl",
    }
    registry = build_model_registry(settings_file.models)
    editors: dict[str, SettingEditor] = {}
    entries = dict(settings_file.settings)
    entries.update(templates)
    for name, entry in entries.items():
        value = entry.value
        if name in {"default_model", "compaction_author_model"}:
            choices = _choices(
                sorted(
                    key
                    for key, model in settings_file.models.items()
                    if "text" in model.capabilities and key not in registry.issues
                )
            )
            if name == "compaction_author_model":
                choices = [SettingChoice(value="none", label="Use default model")] + [
                    choice for choice in choices if choice.value.lower() != "none"
                ]
            else:
                choices = [
                    SettingChoice(value="none", label="Not configured — select a model")
                ] + [choice for choice in choices if choice.value.lower() != "none"]
            editor = SettingEditor(
                kind="select",
                choices=choices,
                aliases={
                    alias: canonical
                    for alias, canonical in registry.canonical_names.items()
                    if canonical in {choice.value for choice in choices}
                },
            )
        elif name == "default_context_script":
            editor = SettingEditor(
                kind="select",
                choices=[SettingChoice(value="", label="No context script")]
                + _context_choices(),
            )
        elif name in _THINKING_SETTINGS:
            editor = SettingEditor(
                kind="select",
                choices=_choices(
                    (
                        "default",
                        "on",
                        "off",
                        "minimal",
                        "low",
                        "medium",
                        "high",
                        "xhigh",
                    )
                ),
            )
        elif name in _FIXED_CHOICES:
            editor = SettingEditor(
                kind="select", choices=_choices(_FIXED_CHOICES[name])
            )
        elif name in web_choices:
            editor = SettingEditor(
                kind="select",
                choices=_choices(web_strategy_registry.names(web_choices[name])),
            )
        elif name in {"disabled_tools", "editable_builtin_providers"}:
            names = (
                sorted(set(get_tools_config()) - RETIRED_BUILTIN_TOOL_NAMES)
                if name == "disabled_tools"
                else sorted(
                    key
                    for key, provider in settings_file.providers.items()
                    if not provider.user_editable
                )
            )
            editor = SettingEditor(
                kind="multiselect", choices=_choices(names), unique_items=True
            )
        elif name in {
            "ingestion_pdf_default_strategies",
            "ingestion_image_default_strategies",
        }:
            names = (
                ["pdf_ocr", "pdf_text"]
                if name == "ingestion_pdf_default_strategies"
                else ["image_ocr"]
            )
            editor = SettingEditor(
                kind="ordered_list",
                choices=_choices(names),
                min_items=1,
                unique_items=True,
                help="Strategies run in priority order. Keep at least one strategy.",
            )
        elif isinstance(value, bool):
            editor = SettingEditor(
                kind="boolean",
                choices=[
                    SettingChoice(value="true", label="On"),
                    SettingChoice(value="false", label="Off"),
                ],
            )
        elif isinstance(value, int | float):
            minimum, maximum = _BOUNDS.get(name, (0, None))
            editor = SettingEditor(
                kind="integer" if isinstance(value, int) else "number",
                minimum=minimum,
                maximum=maximum,
                exclusive_minimum=name in _POSITIVE_SETTINGS,
            )
        elif isinstance(value, list):
            editor = SettingEditor(
                kind="string_list", unique_items=name == "openrouter_ignored_providers"
            )
        elif isinstance(value, dict):
            editor = SettingEditor(kind="json")
        else:
            editor = SettingEditor(kind="text")
        editors[name] = editor
    return editors


def canonical_value(name: str, value: Any) -> Any:
    """Recognize supported legacy spellings without rewriting stored values."""
    if name == "default_context_script":
        return (
            value.strip() if isinstance(value, str) else "" if value is None else value
        )
    if name in _THINKING_SETTINGS:
        return thinking_value_to_label(
            normalize_thinking_value(value, source_name=name)
        )
    if name == "compaction_author_model" and (
        value is None or str(value).strip().lower() in {"", "none"}
    ):
        return "none"
    if isinstance(value, str):
        if (
            name == "compaction_strategy"
            and value.strip().lower() == "stepped_session_map"
        ):
            return "session_map"
        if (
            name in _FIXED_CHOICES
            or name.startswith("web_")
            or name in {"default_model", "compaction_author_model"}
        ):
            return value.strip().lower()
    return value


def value_issues(name: str, value: Any, editor: SettingEditor) -> list[SettingIssue]:
    """Validate a configured value without coercing away an invalid type."""
    try:
        normalized = canonical_value(name, value)
    except ValueError as exc:
        return [SettingIssue(code="choice", message=str(exc))]
    message = ""
    code = "type"
    if editor.kind == "boolean":
        if not isinstance(value, bool):
            message = "Value must be a boolean (On or Off)."
    elif editor.kind in {"integer", "number"}:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or (editor.kind == "integer" and not isinstance(value, int))
        ):
            message = (
                "Value must be an integer."
                if editor.kind == "integer"
                else "Value must be a number."
            )
        elif isinstance(value, float) and not math.isfinite(value):
            message = "Value must be a finite number."
        elif editor.minimum is not None and (
            value < editor.minimum
            or (editor.exclusive_minimum and value == editor.minimum)
        ):
            code = "range"
            message = f"Value must be {'greater than' if editor.exclusive_minimum else 'at least'} {editor.minimum:g}."
        elif editor.maximum is not None and value > editor.maximum:
            code = "range"
            message = f"Value must be at most {editor.maximum:g}."
    elif editor.kind == "select":
        available = {choice.value for choice in editor.choices}
        if isinstance(normalized, str):
            normalized = editor.aliases.get(normalized, normalized)
        if not isinstance(normalized, str) or normalized not in available:
            code = "choice"
            message = (
                f"Saved value {value!r} is unavailable. Select an available option."
            )
    elif editor.kind in {"multiselect", "ordered_list", "string_list"}:
        if not isinstance(value, list):
            message = "Value must be a list of strings."
        elif any(not isinstance(item, str) or not item.strip() for item in value):
            message = "Each list entry must be a non-empty string."
        elif len(value) < editor.min_items:
            message = f"Select at least {editor.min_items} item(s)."
        elif editor.unique_items and len(set(value)) != len(value):
            message = "List entries must not be repeated."
        elif editor.kind != "string_list":
            invalid = [
                item
                for item in value
                if item not in {choice.value for choice in editor.choices}
            ]
            if invalid:
                code = "choice"
                message = f"Unavailable list entries: {', '.join(invalid)}. Remove or replace them."
    elif editor.kind == "json" and not isinstance(value, dict):
        message = "Value must be a JSON object."
    elif editor.kind == "text" and not isinstance(value, str):
        message = "Value must be text."
    return [SettingIssue(code=code, message=message)] if message else []


def settings_issues(
    entries: dict[str, SettingsEntry], editors: dict[str, SettingEditor]
) -> dict[str, list[SettingIssue]]:
    issues = {
        name: value_issues(name, entry.value, editors[name])
        for name, entry in entries.items()
    }
    for lower, upper, strict in _RELATIONSHIPS:
        if lower == "compaction_low_watermark_tokens":
            strategy = entries.get("compaction_strategy")
            if (
                strategy is None
                or canonical_value("compaction_strategy", strategy.value)
                != "session_map"
            ):
                continue
        if (
            lower not in entries
            or upper not in entries
            or issues[lower]
            or issues[upper]
        ):
            continue
        low, high = entries[lower].value, entries[upper].value
        if low > high or (strict and low == high):
            message = f"{lower} must be {'less than' if strict else 'at most'} {upper}."
            for key in (lower, upper):
                issues[key].append(SettingIssue(code="relationship", message=message))
    return issues


def parse_setting_value(name: str, raw: str, editor: SettingEditor) -> Any:
    """Parse the existing string update protocol using the product-owned type."""
    if editor.kind == "boolean":
        if raw.strip().lower() in {"true", "1", "yes", "on"}:
            return True
        if raw.strip().lower() in {"false", "0", "no", "off"}:
            return False
        raise ValueError("Value must be true or false.")
    if editor.kind == "integer":
        if not re.fullmatch(r"[+-]?[0-9]+", raw.strip()):
            raise ValueError("Value must be an integer.")
        return int(raw.strip())
    if editor.kind == "number":
        try:
            return float(raw.strip())
        except ValueError as exc:
            raise ValueError("Value must be a number.") from exc
    if editor.kind in {"multiselect", "ordered_list", "string_list", "json"}:
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Value must be valid JSON for this setting.") from exc
    return canonical_value(name, raw)


def validate_update(
    name: str,
    value: Any,
    entries: dict[str, SettingsEntry],
    editors: dict[str, SettingEditor],
) -> None:
    """Reject invalid changes, while permitting unrelated repairs of saved data."""
    errors = value_issues(name, value, editors[name])
    if errors:
        raise ValueError(errors[0].message)
    changed = dict(entries)
    changed[name] = entries[name].model_copy(update={"value": value})
    after = settings_issues(changed, editors)
    if name == "compaction_strategy" and value == "session_map":
        for key in (
            "compaction_low_watermark_tokens",
            "compaction_high_watermark_tokens",
        ):
            if after.get(key):
                raise ValueError(after[key][0].message)
    if not after[name]:
        return
    # Permit incremental repairs to a pre-existing relationship violation.
    for lower, upper, _strict in _RELATIONSHIPS:
        if name not in {lower, upper} or lower not in entries or upper not in entries:
            continue
        if not any(issue.code == "relationship" for issue in after[name]):
            continue
        old_low, old_high = entries[lower].value, entries[upper].value
        if type(old_low) not in {int, float} or type(old_high) not in {int, float}:
            continue
        if old_low - old_high > changed[lower].value - changed[upper].value:
            return
    raise ValueError(after[name][0].message)
