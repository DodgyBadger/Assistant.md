"""Atomic managed model catalog upgrades with conservative legacy adoption."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.settings.model_registry import (
    build_model_registry,
    normalize_model_alias,
    preserve_renamed_alias,
)

# Exact legacy binding used only to retire the former shipped Jev entry.
_LEGACY_JEV_BINDING = ("typesafe", "jev-latest", {"decision"})

_RETIRED_MANAGED_IDS = frozenset({"builtin-jev"})

# Standard aliases identify managed families across generations. Custom aliases
# are outside this policy, even when their provider binding matches a family.
_MANAGED_FAMILIES = {
    "builtin-sonnet": r"claude-(?:sonnet-[\d-]+|[\d-]+-sonnet(?:-[\d-]+)?)(?:-latest)?",
    "builtin-opus": r"claude-(?:opus-[\d-]+|[\d-]+-opus(?:-[\d-]+)?)(?:-latest)?",
    "builtin-haiku": r"claude-(?:haiku-[\d-]+|[\d-]+-haiku(?:-[\d-]+)?)(?:-latest)?",
    "builtin-astra": r"gpt-[\d.]+-astra(?:-latest|-[\d-]+)?",
    "builtin-gpt": r"gpt-[\d.]+o?(?:-sol|-turbo)?(?:-latest|-preview|-\d{4}(?:-\d{2}-\d{2})?)?",
    "builtin-gpt-mini": r"gpt-[\d.]+(?:o)?-(?:mini|terra)(?:-latest|-\d{4}-\d{2}-\d{2})?",
    "builtin-gpt-nano": r"gpt-[\d.]+-(?:nano|luna)(?:-latest|-\d{4}-\d{2}-\d{2})?",
    "builtin-gemini": r"gemini-(?:[\d.]+-)?pro(?:-latest|-preview(?:-[\d-]+)?|-[\d-]+)?",
    "builtin-gemini-flash": r"gemini-(?:[\d.]+-)?flash(?:-latest|-preview(?:-[\d-]+)?|-[\d-]+)?",
    "builtin-gemini-flash-lite": r"gemini-(?:[\d.]+-)?flash-lite(?:-latest|-preview(?:-[\d-]+)?|-[\d-]+)?",
    "builtin-grok": r"grok-[\d.]+(?:-latest|-[\d-]+)?",
    "builtin-grok-fast": r"grok-(?:4\.3|[\d.-]+-fast(?:-reasoning|-non-reasoning)?)(?:-latest)?",
    "builtin-mistral-large": r"mistral-large(?:-[\d-]+|-latest)?",
    "builtin-mistral-medium": r"mistral-medium(?:-[\d-]+|-latest)?",
    "builtin-mistral-small": r"mistral-small(?:-[\d-]+|-latest)?",
}


@dataclass(frozen=True)
class CatalogUpgrade:
    models: dict[str, Any]
    renames: list[tuple[str, str]]
    updated: list[str]
    removed: list[str]
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

    Standard aliases are adopted when provider and model family match, including
    explicitly unmanaged entries. Custom aliases retain their independent binding.
    """
    models = deepcopy(active)
    renames: list[tuple[str, str]] = []
    updated: list[str] = []
    removed: list[str] = []
    issues: list[str] = []
    catalog_ids: dict[str, str] = {}
    standard_targets: dict[str, dict[str, Any]] = {}
    for name, entry in template.items():
        if not isinstance(entry, dict) or not entry.get("managed_id"):
            continue
        identity = entry["managed_id"]
        if identity in catalog_ids:
            issues.append(f"Managed identity '{identity}' is repeated in the catalog.")
        catalog_ids[identity] = name
        if identity in _MANAGED_FAMILIES:
            standard_targets[normalize_model_alias(name)] = entry
            standard_targets[identity.removeprefix("builtin-")] = entry

    seen_ids: set[str] = set()
    for old_name, entry in list(models.items()):
        if not isinstance(entry, dict):
            continue
        if entry.get("managed_id") in _RETIRED_MANAGED_IDS:
            models.pop(old_name)
            removed.append(old_name)
            continue
        standard = standard_targets.get(normalize_model_alias(old_name))
        if not entry.get("managed_id") and standard is not None:
            binding = entry.get("model_string")
            if (
                entry.get("provider") != standard["provider"]
                or not isinstance(binding, str)
                or re.fullmatch(
                    _MANAGED_FAMILIES[standard["managed_id"]], binding.strip().lower()
                )
                is None
            ):
                issues.append(
                    f"Standard model alias '{old_name}' has a different provider or model family. "
                    f"Rename the custom entry and remove '{old_name}' from its alternate aliases, "
                    "or restore the catalog provider and model family to use the managed model."
                )
                continue
            entry["managed_id"] = standard["managed_id"]
        if "managed_id" not in entry:
            capabilities = entry.get("capabilities", ["text"])
            if not isinstance(capabilities, list):
                capabilities = [capabilities]
            binding = (
                entry.get("provider"),
                entry.get("model_string"),
                {str(cap).strip().lower() for cap in capabilities},
            )
            if old_name == "jev" and binding == _LEGACY_JEV_BINDING:
                models.pop(old_name)
                removed.append(old_name)
                continue
            entry["managed_id"] = None
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
    if updated or renames or removed:
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
        return CatalogUpgrade(deepcopy(active), [], [], [], issues)
    return CatalogUpgrade(models, renames, updated, removed, [])
