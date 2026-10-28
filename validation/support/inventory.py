"""Import-free scenario discovery and explicit accounting for legacy test suites."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scenario_harness.discovery import describe_scenario as generic_describe_scenario
from scenario_harness.project import InventoryError

from validation.support.catalog import validate_catalog
from validation.support.pytest_admission import load_staging_admissions


@dataclass(frozen=True)
class ScenarioDefinition:
    selector: str
    path: str
    class_name: str


@dataclass
class InventoryReport:
    scenarios: list[ScenarioDefinition] = field(default_factory=list)
    standalone_experiments: list[str] = field(default_factory=list)
    legacy_test_files: int = 0
    legacy_named_tests: int = 0
    legacy_locations: dict[str, int] = field(default_factory=dict)
    disposition_counts: dict[str, int] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    scenario_governance: dict[str, object] = field(default_factory=dict)
    pytest_staging: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "scenario_count": len(self.scenarios),
            "scenarios": [asdict(item) for item in self.scenarios],
            "standalone_experiments": self.standalone_experiments,
            "legacy_test_files": self.legacy_test_files,
            "legacy_named_tests": self.legacy_named_tests,
            "legacy_locations": self.legacy_locations,
            "disposition_counts": self.disposition_counts,
            "structural_inventory_complete": not self.issues,
            "behavioral_coverage_complete": False,
            "issues": self.issues,
            "scenario_governance": self.scenario_governance,
            "pytest_staging": self.pytest_staging,
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


def _standalone_scripts(root: Path) -> set[str]:
    # Run artifacts may use a temporary root with a link to the source scenarios.
    source_root = (root / "scenarios").resolve().parent
    policy = source_root / "registry" / "inventory-policy.json"
    if not policy.exists():
        return set()
    data = _read_json(policy)
    scripts = [_text(item) for item in _items(data.get("standalone_experiments"))]
    if len(scripts) != len(set(scripts)):
        raise InventoryError("Duplicate standalone experiment registration")
    for script in scripts:
        path = Path(script)
        if (
            path.is_absolute()
            or ".." in path.parts
            or not script.startswith("scenarios/experiments/")
            or path.suffix != ".py"
        ):
            raise InventoryError(f"Invalid standalone experiment path: {script}")
        if not (root / path).is_file():
            raise InventoryError(
                f"Registered standalone experiment is missing: {script}"
            )
    return set(scripts)


def describe_scenario(path: Path, scenarios_root: Path) -> ScenarioDefinition:
    definition = generic_describe_scenario(
        path, scenarios_root, base_class="validation.support.base_scenario:BaseScenario"
    )
    assert definition.class_name is not None
    return ScenarioDefinition(
        definition.selector, definition.path, definition.class_name
    )


def collect_scenarios(root: Path) -> tuple[list[ScenarioDefinition], list[str]]:
    """Read declarations without importing scenarios or touching application state."""
    root = root.resolve()
    scripts = _standalone_scripts(root)
    scenarios_root = root / "scenarios"
    if not scenarios_root.is_dir():
        raise InventoryError("Scenario directory is missing")
    definitions = []
    issues = []
    for path in sorted(scenarios_root.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        relative = path.relative_to(root).as_posix()
        if relative in scripts:
            continue
        try:
            definitions.append(describe_scenario(path, scenarios_root))
        except InventoryError as exc:
            issues.append(str(exc))
    if issues:
        raise InventoryError("\n".join(issues))
    if not definitions:
        raise InventoryError("No scenarios discovered")
    return definitions, sorted(scripts)


class ScenarioCatalog:
    """Compatibility selection surface without runtime imports or run artifacts."""

    def __init__(self, root: Path):
        self.root = root

    def discover_scenarios(self) -> list[str]:
        definitions, _ = collect_scenarios(self.root)
        return [item.selector for item in definitions]


def named_tests(path: Path) -> list[str]:
    """Account for supported root Python functions and literal Node test labels."""
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        tree = ast.parse(source, filename=str(path))
        if any(
            (isinstance(node, ast.ClassDef) and node.name.startswith("Test"))
            or (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name.startswith("test_")
                and node not in tree.body
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
            r"\b(?:test|it)\s*(?:\.\s*\w+\s*)*\(", source, flags=re.MULTILINE
        )
        tests = [
            match.group(2)
            for match in re.finditer(
                r"^\s*test\(\s*(['\"])(.*?)\1\s*,", source, flags=re.MULTILINE
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


def _check_pytest_staging(root: Path, report: InventoryReport) -> set[str]:
    """Admit exact shadow sources without importing candidate Python code."""
    manifest = root / "registry" / "pytest-staging.json"
    if not manifest.exists():
        return set()
    try:
        entries = load_staging_admissions(root)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InventoryError(str(error)) from error
    admitted = {entry.relative for entry in entries}
    for entry in entries:
        relative = entry.relative
        if not entry.hash_matches:
            report.issues.append(
                f"Pytest staging source changed since admission: {relative}"
            )
        if entry.kind == "behavioral":
            actual = named_tests(entry.source)
            if Counter(actual) != Counter(entry.tests):
                report.issues.append(f"Pytest staging declarations differ: {relative}")
            report.pytest_staging.append(
                {
                    "path": relative,
                    "nodes": [f"validation/{relative}::{name}" for name in actual],
                    "coverage_status": "shadow_pending_review",
                }
            )
    staged_root = root / "pytest_scenarios"
    actual_sources = {
        path.relative_to(root).as_posix()
        for path in staged_root.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ini", ".toml"}
        and "__pycache__" not in path.parts
    }
    for relative in sorted(actual_sources - admitted):
        report.issues.append(f"Unregistered pytest staging source: {relative}")
    return admitted


def _check_legacy_tests(
    root: Path, report: InventoryReport, admitted_pytest: set[str]
) -> None:
    actual = {
        "validation/" + path.relative_to(root).as_posix(): path
        for path in root.rglob("test_*")
        if path.is_file()
        and path.relative_to(root).as_posix() not in admitted_pytest
        and path.relative_to(root).parts[0] not in {"runs", "scenarios"}
        and not (
            path.relative_to(root).parts[0] == "fixtures"
            and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        )
        and not any(
            part.startswith(".") or part == "__pycache__"
            for part in path.relative_to(root).parts
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


def build_inventory(root: Path) -> InventoryReport:
    """Structural accounting only; a complete inventory is not proof of coverage."""
    root = root.resolve()
    report = InventoryReport()
    try:
        report.scenarios, report.standalone_experiments = collect_scenarios(root)
    except InventoryError as exc:
        report.issues.extend(str(exc).splitlines())
    try:
        admitted_pytest = _check_pytest_staging(root, report)
        _check_legacy_tests(root, report, admitted_pytest)
    except (OSError, SyntaxError, UnicodeError, InventoryError) as exc:
        report.issues.append(str(exc))
    try:
        source_root = (root / "scenarios").resolve().parent
        policy_path = source_root / "registry/inventory-policy.json"
        if policy_path.exists():
            policy = _read_json(policy_path)
            catalog = policy.get("scenario_catalog")
            if catalog is not None:
                if catalog != "scenario-catalog.json":
                    raise InventoryError("Unsupported scenario catalog policy")
                report.scenario_governance = validate_catalog(
                    source_root,
                    {
                        item.selector: source_root / "scenarios" / f"{item.selector}.py"
                        for item in report.scenarios
                    },
                    registry_root=source_root / "registry",
                )
                report.issues.extend(
                    str(issue) for issue in _items(report.scenario_governance["issues"])
                )
    except (OSError, ValueError) as exc:
        report.issues.append(str(exc))
    return report
