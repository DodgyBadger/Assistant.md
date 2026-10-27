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


def contract_fingerprint(contract: Mapping[str, object]) -> str:
    """Identify the requirement and proof boundary independently of formatting."""
    payload = {
        name: _text(contract.get(name))
        for name in ("id", "requirement", "proof_surface")
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _review_evidence(
    root: Path,
    contract: Mapping[str, object],
    selectors: list[str],
    sources: Mapping[str, Path],
) -> None:
    """Validate evidence bindings and claimed observations, never grant approval."""
    identity = _text(contract.get("id"))
    references = _unique_text(contract.get("review_evidence"))
    if not references or not selectors:
        raise CatalogError(f"Contract has no pilot review evidence: {identity}")
    covered: set[str] = set()
    governance_root = (root / "governance").resolve()
    for reference in references:
        relative = PurePosixPath(reference)
        path = governance_root / reference
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix != ".json"
            or not path.resolve().is_relative_to(governance_root)
        ):
            raise CatalogError(f"Invalid pilot evidence path: {identity}")
        evidence = _object(
            json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
        )
        if (
            type(evidence.get("schema_version")) is not int
            or evidence["schema_version"] != 1
        ):
            raise CatalogError(f"Unsupported pilot evidence schema: {identity}")
        selector = _selector(evidence.get("scenario"))
        if evidence.get("contract_id") != identity or selector not in selectors:
            raise CatalogError(f"Pilot evidence identity mismatch: {identity}")
        if evidence.get("contract_fingerprint") != contract_fingerprint(contract):
            raise CatalogError(
                f"Pilot evidence has a stale contract boundary: {identity}"
            )
        if (
            evidence.get("scenario_sha256")
            != hashlib.sha256(sources[selector].read_bytes()).hexdigest()
        ):
            raise CatalogError(
                f"Pilot evidence has a stale scenario identity: {selector}"
            )
        if not re.fullmatch(r"[a-f0-9]{40}", _text(evidence.get("source_revision"))):
            raise CatalogError(
                f"Pilot evidence lacks source revision identity: {identity}"
            )
        for name in ("proof_summary", "exclusions"):
            if not _unique_text(evidence.get(name)):
                raise CatalogError(f"Pilot evidence lacks {name}: {identity}")
        baseline = _object(_object(evidence.get("baseline")).get("observation"))
        if (
            baseline.get("status") != "passed"
            or baseline.get("scenario_name") != selector
            or baseline.get("execution_phase") is not None
            or baseline.get("error_message") is not None
            or baseline.get("timed_out", False) is not False
            or baseline.get("interrupted", False) is not False
        ):
            raise CatalogError(
                f"Pilot evidence lacks a clean passing observation: {identity}"
            )
        examples = _array(evidence.get("negative_examples"))
        if not examples:
            raise CatalogError(
                f"Pilot evidence lacks discriminating negative examples: {identity}"
            )
        for value in examples:
            example = _object(value)
            _text(example.get("fault"))
            _text(example.get("assertion"))
            observation = _object(example.get("observation"))
            if (
                observation.get("status") != "failed"
                or observation.get("execution_phase") != "execution"
                or observation.get("scenario_name") != selector
            ):
                raise CatalogError(
                    f"Pilot negative example is not a behavioral assertion failure: {identity}"
                )
            _text(observation.get("error_message"))
        covered.add(selector)
    if covered != set(selectors):
        raise CatalogError(
            f"Pilot review evidence does not cover every linked scenario: {identity}"
        )


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
        for identity, contract in contracts.items():
            _review_evidence(root, contract, matrix[identity], sources)
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
