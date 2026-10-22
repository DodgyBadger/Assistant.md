"""Atomic managed model catalog upgrades with conservative legacy adoption."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.settings.model_registry import (
    build_model_registry,
    normalize_model_alias,
    preserve_renamed_alias,
)

# Verified shipped bindings before managed identities were introduced. Keep these
# baselines fixed when changing the catalog so local retargeting is never adopted.
_LEGACY_BINDINGS: dict[str, tuple[str, str, set[str]]] = {
    "gemini": ("google", "gemini-3.1-pro-preview", {"text", "vision"}),
    "gemini-flash": ("google", "gemini-flash-latest", {"text", "vision"}),
    "gemini-flash-lite": ("google", "gemini-flash-lite-latest", {"text", "vision"}),
    "sonnet": ("anthropic", "claude-sonnet-5", {"text", "vision"}),
    "opus": ("anthropic", "claude-opus-5", {"text", "vision"}),
    "haiku": ("anthropic", "claude-haiku-4-5", {"text", "vision"}),
    "astra": ("openai", "gpt-6-astra", {"text", "vision"}),
    "gpt": ("openai", "gpt-5.6-sol", {"text", "vision"}),
    "gpt-mini": ("openai", "gpt-5.6-terra", {"text", "vision"}),
    "gpt-nano": ("openai", "gpt-5.6-luna", {"text", "vision"}),
    "grok": ("grok", "grok-4.6-latest", {"text", "vision"}),
    "grok-fast": ("grok", "grok-4.3-latest", {"text", "vision"}),
    "mistral-large": ("mistral", "mistral-large-latest", {"text", "vision"}),
    "mistral-small": ("mistral", "mistral-small-latest", {"text", "vision"}),
    "mistral-medium": ("mistral", "mistral-medium-latest", {"text", "vision"}),
    "jev": ("typesafe", "jev-latest", {"decision"}),
}


@dataclass(frozen=True)
class CatalogUpgrade:
    models: dict[str, Any]
    renames: list[tuple[str, str]]
    updated: list[str]
    issues: list[str]


@dataclass(frozen=True)
class _ModelNames:
    alternate_aliases: list[str]


def _names(models: dict[str, Any]) -> dict[str, set[str]]:
    names: dict[str, set[str]] = {}
    for canonical, entry in models.items():
        if not isinstance(entry, dict):
            continue
        aliases = entry.get("alternate_aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) for alias in aliases
        ):
            continue
        for alias in [canonical, *aliases]:
            names.setdefault(normalize_model_alias(alias), set()).add(canonical)
    return names


def upgrade_model_catalog(
    active: dict[str, Any], template: dict[str, Any]
) -> CatalogUpgrade:
    """Update existing managed identities; leave missing entries to settings repair.

    New custom entries explicitly persist managed_id=None. Legacy entries without
    that field are adopted only against the frozen shipped baseline.
    """
    models = deepcopy(active)
    renames: list[tuple[str, str]] = []
    updated: list[str] = []
    issues: list[str] = []
    catalog_ids: dict[str, str] = {}
    for name, entry in template.items():
        if not isinstance(entry, dict) or not entry.get("managed_id"):
            continue
        identity = entry["managed_id"]
        if identity in catalog_ids:
            issues.append(f"Managed identity '{identity}' is repeated in the catalog.")
        catalog_ids[identity] = name

    seen_ids: set[str] = set()
    for old_name, entry in list(models.items()):
        if not isinstance(entry, dict):
            continue
        if "managed_id" not in entry:
            baseline = _LEGACY_BINDINGS.get(old_name)
            target = template.get(old_name, {})
            if not target.get("managed_id"):
                target = template.get(catalog_ids.get(f"builtin-{old_name}", ""), {})
            capabilities = entry.get("capabilities", ["text"])
            if not isinstance(capabilities, list):
                capabilities = [capabilities]
            binding = (
                entry.get("provider"),
                entry.get("model_string"),
                {str(cap).strip().lower() for cap in capabilities},
            )
            entry["managed_id"] = (
                target.get("managed_id") if baseline == binding else None
            )
            if entry["managed_id"] is None and baseline is not None:
                entry["user_editable"] = True
        identity = entry.get("managed_id")
        if not identity:
            continue
        if identity in seen_ids:
            issues.append(f"Managed identity '{identity}' is repeated in settings.")
            continue
        seen_ids.add(identity)
        new_name = catalog_ids.get(identity)
        if new_name is None:
            # Retain retired entries and their aliases until explicitly removed.
            continue
        target = template[new_name]
        owners = _names(models).get(normalize_model_alias(new_name), set())
        if owners - {old_name}:
            issues.append(
                f"Cannot update managed model '{old_name}': alias '{new_name}' belongs to another model."
            )
            continue
        aliases = entry.get("alternate_aliases", [])
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) for alias in aliases
        ):
            issues.append(f"Managed model '{old_name}' has an invalid alternate list.")
            continue
        replacement = deepcopy(entry)
        for field in ("provider", "model_string", "capabilities"):
            replacement[field] = deepcopy(target[field])
        replacement["user_editable"] = False
        if old_name != new_name:
            replacement["alternate_aliases"] = preserve_renamed_alias(
                old_name, new_name, aliases
            )
            renames.append((old_name, new_name))
        if replacement != active.get(old_name) or old_name != new_name:
            updated.append(new_name)
        if old_name != new_name:
            models.pop(old_name)
        models[new_name] = replacement
    if updated or renames:
        entries: dict[str, _ModelNames] = {}
        for name, configured in models.items():
            if not isinstance(configured, dict):
                continue
            aliases = configured.get("alternate_aliases", [])
            if not isinstance(aliases, list) or any(
                not isinstance(alias, str) for alias in aliases
            ):
                issues.append(f"Model '{name}' has an invalid alternate list.")
                continue
            entries[name] = _ModelNames(aliases)
        registry = build_model_registry(entries)
        issues.extend(
            message for messages in registry.issues.values() for message in messages
        )
    if issues:
        return CatalogUpgrade(deepcopy(active), [], [], issues)
    return CatalogUpgrade(models, renames, updated, [])
