"""Normalized, one-hop model identities shared by configuration and execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


class ModelAliases(Protocol):
    @property
    def alternate_aliases(self) -> list[str]: ...


class UnknownModelAlias(ValueError):
    """The requested name is absent from the model registry."""


class InvalidModelAlias(ValueError):
    """The requested name belongs to an invalid or ambiguous entry."""


def normalize_model_alias(name: str) -> str:
    return name.strip().lower()


def preserve_renamed_alias(
    old_name: str, new_name: str, alternates: list[str]
) -> list[str]:
    """Promote a same-model alternate and retain the prior canonical exactly once."""
    if old_name == new_name:
        return list(alternates)
    old_key, new_key = normalize_model_alias(old_name), normalize_model_alias(new_name)
    retained = [
        alias for alias in alternates if normalize_model_alias(alias) != new_key
    ]
    if old_key != new_key and old_key not in {
        normalize_model_alias(alias) for alias in retained
    }:
        retained.append(old_name)
    return retained


@dataclass(frozen=True)
class ModelRegistry:
    canonical_names: dict[str, str]
    issues: dict[str, list[str]]
    invalid_names: frozenset[str]

    def resolve(self, name: str) -> str:
        key = normalize_model_alias(name)
        if key in self.invalid_names:
            raise InvalidModelAlias(f"Model alias '{name}' has invalid configuration.")
        if key not in self.canonical_names:
            raise UnknownModelAlias(f"Unknown model '{name}'.")
        return self.canonical_names[key]

    def validate(self) -> None:
        if self.issues:
            raise ValueError(
                " ".join(
                    message for messages in self.issues.values() for message in messages
                )
            )


def build_model_registry(models: Mapping[str, ModelAliases]) -> ModelRegistry:
    """Exclude invalid entries without letting ambiguous names choose a winner."""
    owners: dict[str, list[str]] = {}
    issues: dict[str, list[str]] = {}

    def issue(owner: str, message: str) -> None:
        issues.setdefault(owner, []).append(message)

    for canonical, model in models.items():
        seen: set[str] = set()
        for index, name in enumerate([canonical, *model.alternate_aliases]):
            key = normalize_model_alias(name)
            if not key or key == "none" or (key == "test" and index > 0):
                issue(
                    canonical,
                    f"Model '{canonical}' uses reserved or empty alias {name!r}.",
                )
            if key in seen:
                issue(canonical, f"Model '{canonical}' repeats alias {name!r}.")
            seen.add(key)
            if canonical not in owners.setdefault(key, []):
                owners[key].append(canonical)
    for key, entries in owners.items():
        if len(entries) > 1:
            for owner in entries:
                issue(owner, f"Model alias '{key}' is shared by: {', '.join(entries)}.")
    invalid_names = frozenset(
        key
        for key, entries in owners.items()
        if any(owner in issues for owner in entries)
    )
    return ModelRegistry(
        canonical_names={
            key: entries[0]
            for key, entries in owners.items()
            if key not in invalid_names
        },
        issues=issues,
        invalid_names=invalid_names,
    )
