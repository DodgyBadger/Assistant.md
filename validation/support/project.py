"""AssistantMD ownership of collection policy and project configuration."""

from pathlib import Path

from scenario_harness.project import ProjectSpec
from scenario_harness.project import load_project as read_project

from .inventory import (
    InventoryReport,
    ScenarioDefinition,
    build_inventory,
    collect_scenarios,
)


def load_project() -> ProjectSpec:
    return read_project(Path(__file__).resolve().parents[1] / "project.toml")


def collect(project: ProjectSpec) -> list[ScenarioDefinition]:
    definitions, _ = collect_scenarios(project.suite_root)
    return definitions


def check_inventory(project: ProjectSpec, root: Path | None = None) -> InventoryReport:
    source_root = (project.scenarios_dir.resolve()).parent
    return build_inventory(root if root is not None else source_root)


def format_inventory(report: InventoryReport) -> str:
    lines = [
        f"Scenarios: {len(report.scenarios)}",
        f"Standalone opt-in experiment scripts: {len(report.standalone_experiments)}",
        f"Legacy standalone suites: {report.legacy_test_files} files, {report.legacy_named_tests} named tests",
        *[
            f"  {location}: {count} files"
            for location, count in report.legacy_locations.items()
        ],
        "Legacy accounting is not execution or proof of complete behavioral coverage.",
        "Structural inventory: " + ("FAILED" if report.issues else "complete"),
    ]
    if governance := report.scenario_governance:
        lines.extend(
            [
                f"Contract pilots: {governance['pilot_scenarios']}; exact legacy scenarios: {governance['legacy_scenarios']}; required contracts: {governance['required_contracts']}",
                "Catalog registration does not establish semantic admission or external approval.",
            ]
        )
    lines.extend(f"- {issue}" for issue in report.issues)
    return "\n".join(lines)
