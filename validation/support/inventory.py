"""Import-free accounting for canonical pytest sources and archived tests."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from validation.support.pytest_admission import load_admissions


class InventoryError(ValueError):
    """Source declarations or archive accounting are invalid."""


@dataclass
class InventoryReport:
    legacy_test_files: int = 0
    legacy_named_tests: int = 0
    legacy_locations: dict[str, int] = field(default_factory=dict)
    disposition_counts: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    pytest_admission: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            **asdict(self),
            "behavioral_source_count": len(self.pytest_admission),
            "structural_inventory_complete": not self.issues,
            "behavioral_coverage_complete": False,
        }


def _record(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise InventoryError("Expected a JSON object with string keys")
    return dict(value)


def _items(value: object) -> list[object]:
    if not isinstance(value, list):
        raise InventoryError("Expected a JSON array")
    return list(value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InventoryError("Expected nonempty text")
    return value


def _read_json(path: Path) -> dict[str, object]:
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
        record = _record(value)
        if record.get("schema_version") != 1:
            raise InventoryError("Unsupported schema_version")
        return record
    except (OSError, ValueError) as exc:
        raise InventoryError(f"{path.name}: {exc}") from exc


def named_tests(path: Path) -> list[str]:
    """Account for supported root Python functions and literal Node test labels."""
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        tree = ast.parse(source, filename=str(path))
        if any(
            isinstance(node, ast.ClassDef)
            and node.name.startswith("Test")
            or (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name.startswith("test_")
                and (node not in tree.body)
            )
            for node in ast.walk(tree)
        ):
            raise InventoryError(
                f"{path.name}: unsupported class/nested test declarations"
            )
        tests = [
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test_")
        ]
    elif path.suffix == ".cjs":
        declarations = re.findall(
            "\\b(?:test|it)\\s*(?:\\.\\s*\\w+\\s*)*\\(", source, flags=re.MULTILINE
        )
        tests = [
            match.group(2)
            for match in re.finditer(
                "^\\s*test\\(\\s*(['\\\"])(.*?)\\1\\s*,", source, flags=re.MULTILINE
            )
        ]
        if len(tests) != len(declarations):
            raise InventoryError(
                f"{path.name}: unsupported dynamic/multiline Node test label; review inventory support"
            )
    else:
        raise InventoryError(f"{path.name}: unsupported test format")
    if not tests:
        raise InventoryError(
            f"{path.name}: no supported named tests; review inventory support"
        )
    if len(tests) != len(set(tests)):
        raise InventoryError(f"{path.name}: duplicate named tests")
    return tests


def _check_legacy_tests(
    root: Path, report: InventoryReport, admitted_pytest: set[str]
) -> None:
    actual = {
        "validation/" + path.relative_to(root).as_posix(): path
        for path in root.rglob("test_*")
        if path.is_file()
        and path.relative_to(root).as_posix() not in admitted_pytest
        and (path.relative_to(root).parts[0] not in {"runs", "scenarios"})
        and (
            not (
                path.relative_to(root).parts[0] == "fixtures"
                and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
            )
        )
        and (
            not any(
                part.startswith(".") or part == "__pycache__"
                for part in path.relative_to(root).parts
            )
        )
    }
    ledger_path = root / "registry" / "legacy-ledger.json"
    entries = (
        _items(_read_json(ledger_path).get("files")) if ledger_path.exists() else []
    )
    registered = set()
    dispositions: Counter[str] = Counter()
    locations: Counter[str] = Counter()
    for item in entries:
        entry = _record(item)
        name = _text(entry.get("path"))
        if name in registered:
            report.issues.append(f"Duplicate ledger entry: {name}")
        registered.add(name)
        path = actual.get(name)
        if path is None:
            report.issues.append(f"Registered legacy test is missing: {name}")
            continue
        report.legacy_test_files += 1
        relative = path.relative_to(root)
        location = (
            "/".join(relative.parts[:2])
            if relative.parts[0] == "legacy" and len(relative.parts) > 2
            else relative.parts[0] if len(relative.parts) > 1 else "root"
        )
        locations[location] += 1
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry.get("sha256"):
            report.issues.append(f"Legacy test changed since its ledger review: {name}")
        tests = named_tests(path)
        report.legacy_named_tests += len(tests)
        accounted: list[str] = []
        for group_data in _items(entry.get("groups")):
            group = _record(group_data)
            disposition = _text(group.get("disposition"))
            if disposition not in {
                "covered",
                "gap",
                "rewrite",
                "retire_candidate",
                "framework_infrastructure",
            }:
                report.issues.append(f"{name}: unknown disposition {disposition}")
            dispositions[disposition] += 1
            for key in ("id", "contract", "rationale", "proposed_destination"):
                _text(group.get(key))
            accounted.extend(_text(test) for test in _items(group.get("tests")))
        if Counter(accounted) != Counter(tests):
            report.issues.append(
                f"{name}: ledger test names do not match source declarations"
            )
    for name in sorted(actual.keys() - registered):
        report.issues.append(f"Unaccounted legacy test: {name}")
    report.disposition_counts = dict(sorted(dispositions.items()))
    report.legacy_locations = dict(sorted(locations.items()))


def _check_pytest_admission(root: Path, report: InventoryReport) -> set[str]:
    """Admit exact source declarations without importing application code."""
    try:
        entries = load_admissions(root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InventoryError(str(error)) from error
    admitted = {entry.relative for entry in entries}
    for entry in entries:
        if not entry.hash_matches:
            report.issues.append(
                f"Pytest source changed since admission: {entry.relative}"
            )
        if entry.kind == "behavioral":
            actual = named_tests(entry.source)
            if Counter(actual) != Counter(entry.tests):
                report.issues.append(f"Pytest declarations differ: {entry.relative}")
            report.pytest_admission.append(
                {
                    "path": entry.relative,
                    "nodes": [
                        f"validation/{entry.relative}::{name}" for name in actual
                    ],
                    "coverage_status": "registered_pending_parity_review",
                }
            )
    actual_sources = {
        path.relative_to(root).as_posix()
        for path in (root / "scenarios").rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ini", ".toml"}
        and "__pycache__" not in path.parts
    }
    for relative in sorted(actual_sources - admitted):
        report.issues.append(f"Unregistered pytest source: {relative}")
    return admitted


def build_inventory(root: Path) -> InventoryReport:
    """Account for current sources and archives without attesting behavioral parity."""
    root = root.resolve()
    report = InventoryReport()
    try:
        admitted = _check_pytest_admission(root, report)
        _check_legacy_tests(root, report, admitted)
        migration = root / "registry/pytest-migration.json"
        if migration.exists():
            for entry in _items(_read_json(migration).get("core")):
                row = _record(entry)
                source = _text(row.get("source"))
                path = root.parent / source
                if not path.is_file() or hashlib.sha256(
                    path.read_bytes()
                ).hexdigest() != row.get("sha256"):
                    report.issues.append(
                        f"Archived original changed or missing: {source}"
                    )
    except (OSError, SyntaxError, UnicodeError, InventoryError) as error:
        report.issues.append(str(error))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    options = parser.parse_args()
    report = build_inventory(options.root)
    print(json.dumps(report.to_dict(), indent=2))
    return int(bool(report.issues))


if __name__ == "__main__":
    raise SystemExit(main())
