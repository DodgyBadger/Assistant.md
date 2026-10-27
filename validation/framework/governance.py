"""Import-free contract registration and exact legacy scenario accounting."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath


class CatalogError(ValueError):
    pass


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise CatalogError("Expected an object with string keys")
    return dict(value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise CatalogError("Expected an array")
    return list(value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError("Expected nonempty text")
    return value


def _unique_text(value: object) -> list[str]:
    items = [_text(item) for item in _array(value)]
    if len(items) != len(set(items)):
        raise CatalogError("Duplicate catalog references")
    return items


def _selector(value: object) -> str:
    text = _text(value)
    if (
        any(not part.isidentifier() or part.startswith("_") for part in text.split("/"))
        or PurePosixPath(text).is_absolute()
    ):
        raise CatalogError("Invalid scenario selector")
    return text


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise CatalogError("Duplicate JSON key in scenario catalog")
        result[key] = value
    return result


def validate_catalog(root: Path, sources: Mapping[str, Path]) -> dict[str, object]:
    """Check declarations and identities, without asserting semantic adequacy."""
    report: dict[str, object] = {
        "issues": [],
        "contract_matrix": {},
        "legacy_scenarios": 0,
        "pilot_scenarios": 0,
        "required_contracts": 0,
        "semantic_coverage_reviewed": False,
    }
    try:
        data = _object(
            json.loads(
                (root / "governance/scenario-catalog.json").read_text(encoding="utf-8"),
                object_pairs_hook=_pairs,
            )
        )
        if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
            raise CatalogError("Unsupported scenario catalog schema")
        contracts: dict[str, dict[str, object]] = {}
        for value in _array(data.get("contracts")):
            record = _object(value)
            identity = _text(record.get("id"))
            if (
                not re.fullmatch(r"[a-z][a-z0-9_.-]*", identity)
                or identity in contracts
            ):
                raise CatalogError("Invalid or duplicate contract ID")
            for name in ("requirement", "proof_surface", "review_reference"):
                _text(record.get(name))
            contracts[identity] = record
        matrix: dict[str, list[str]] = {identity: [] for identity in contracts}
        registered: set[str] = set()
        legacy = 0
        pilots = 0
        source_root = (root / "scenarios").resolve()
        for value in _array(data.get("scenarios")):
            registration = _object(value)
            selector = _selector(registration.get("selector"))
            if selector in registered:
                raise CatalogError(f"Duplicate scenario registration: {selector}")
            registered.add(selector)
            source = sources.get(selector)
            if source is None or not source.resolve().is_relative_to(source_root):
                raise CatalogError(
                    f"Registered scenario is missing or outside source root: {selector}"
                )
            references = _unique_text(registration.get("contracts"))
            if any(identity not in contracts for identity in references):
                raise CatalogError(f"Unknown contract in scenario: {selector}")
            classification = registration.get("classification")
            if classification == "legacy":
                legacy += 1
                if references:
                    raise CatalogError(
                        "Legacy scenarios cannot establish admitted contract coverage"
                    )
                digest = _text(registration.get("sha256"))
                if (
                    not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or hashlib.sha256(source.read_bytes()).hexdigest() != digest
                ):
                    raise CatalogError(
                        f"Legacy scenario changed since classification: {selector}"
                    )
            elif classification == "pilot":
                pilots += 1
                if not references:
                    raise CatalogError(f"Pilot scenario has no contract: {selector}")
                for identity in references:
                    matrix[identity].append(selector)
            else:
                raise CatalogError(f"Unknown scenario classification: {selector}")
        if set(sources) != registered:
            raise CatalogError(
                "Unregistered scenarios: "
                + ", ".join(sorted(set(sources) - registered))
            )
        profiles = _object(data.get("profiles"))
        if not profiles:
            raise CatalogError("Scenario catalog has no required profile")
        required: set[str] = set()
        for name, value in profiles.items():
            _selector(name)
            profile = _object(value)
            selections = [
                _selector(item) for item in _unique_text(profile.get("selectors"))
            ]
            selected = {
                selector
                for selector in sources
                if any(
                    selector == spec or selector.startswith(spec + "/")
                    for spec in selections
                )
            }
            if not selected or any(
                not any(
                    selector == spec or selector.startswith(spec + "/")
                    for selector in sources
                )
                for spec in selections
            ):
                raise CatalogError(f"Profile has an empty or unknown selection: {name}")
            for identity in _unique_text(profile.get("required_contracts")):
                if identity not in contracts:
                    raise CatalogError(f"Unknown required contract in profile: {name}")
                required.add(identity)
                if not selected.intersection(matrix[identity]):
                    raise CatalogError(
                        f"Required contract has no pilot scenario in profile {name}: {identity}"
                    )
        if not required:
            raise CatalogError("Scenario catalog has no required contracts")
        report.update(
            contract_matrix={k: sorted(v) for k, v in matrix.items()},
            legacy_scenarios=legacy,
            pilot_scenarios=pilots,
            required_contracts=len(required),
        )
    except (OSError, ValueError) as error:
        report["issues"] = [str(error)]
    return report
