"""Project-configured scenario discovery, execution and reporting CLI."""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .discovery import InventoryError
from .project import load_project, resolve_reference

if TYPE_CHECKING:
    from collections.abc import Sequence

    from .project import ProjectSpec


def expand_scenario_paths(
    available: list[str], specs: Sequence[str], *, allow_unmatched: bool = True
) -> list[str]:
    expanded: list[str] = []
    for raw in specs:
        spec = raw.replace("\\", "/")
        if any(ch in spec for ch in "*?[]"):
            matches = sorted(
                name for name in available if fnmatch.fnmatchcase(name, spec)
            )
        elif spec in available:
            matches = [spec]
        else:
            matches = [name for name in available if name.startswith(f"{spec}/")]
        if matches:
            expanded.extend(matches)
        elif allow_unmatched:
            expanded.append(spec)
    return expanded


def _parser(project_supplied: bool) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run project scenario validations")
    parser.add_argument(
        "--project", required=not project_supplied, help="Project configuration path"
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser("run", help="Run validation scenarios")
    run.add_argument("scenarios", nargs="+")
    run.add_argument("--show-passed", action="store_true")
    run.add_argument("--timeout", type=float, default=300)
    run.add_argument("--cleanup-timeout", type=float, default=30)
    run.add_argument("--env", action="append", default=[], metavar="NAME")
    run.add_argument("--secret-env", action="append", default=[], metavar="NAME")
    listing = commands.add_parser("list", help="List available scenarios")
    listing.add_argument("patterns", nargs="*")
    inventory = commands.add_parser("inventory", help="Inspect structural inventory")
    inventory.add_argument("--root")
    inventory.add_argument("--json", action="store_true")
    governance = commands.add_parser("governance", help="Audit governance changes")
    governance.add_argument("--repo", type=Path)
    governance.add_argument("--base-ref", required=True)
    governance.add_argument("--candidate-ref", default="HEAD")
    return parser


def _inventory(project: ProjectSpec, root: Path | None = None) -> Any:
    if project.inventory_hook:
        return resolve_reference(project.inventory_hook, project.root)(project, root)
    return None


def main(
    argv: Sequence[str] | None = None, *, project: ProjectSpec | None = None
) -> int:
    parser = _parser(project is not None)
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1
    try:
        if args.project:
            project = load_project(Path(args.project))
        if project is None:
            raise InventoryError("A project configuration is required")
        if args.command == "governance":
            if not project.governance_hook:
                raise InventoryError("Project has no governance audit hook")
            try:
                report = resolve_reference(project.governance_hook, project.root)(
                    args.repo or project.root, args.base_ref, args.candidate_ref
                )
            except (ValueError, OSError, UnicodeError) as exc:
                raise InventoryError(
                    "Unable to inspect governance Git revisions"
                ) from exc
            print(json.dumps(report, indent=2))
            return 2 if report["review_required"] else 0
        if args.command == "inventory":
            report = _inventory(project, Path(args.root) if args.root else None)
            if report is None:
                inventory_project = (
                    project.replace_suite_root(Path(args.root))
                    if args.root
                    else project
                )
                definitions = inventory_project.collect()
                if args.json:
                    from dataclasses import asdict

                    print(
                        json.dumps(
                            {
                                "scenarios": [asdict(item) for item in definitions],
                                "issues": [],
                            },
                            indent=2,
                        )
                    )
                else:
                    print(f"Scenarios: {len(definitions)}")
                return 0
            if args.json:
                print(json.dumps(report.to_dict(), indent=2))
            elif project.inventory_formatter:
                print(
                    resolve_reference(project.inventory_formatter, project.root)(report)
                )
            else:
                print(f"Scenarios: {len(report.scenarios)}")
                for issue in report.issues:
                    print(f"- {issue}")
            return 1 if report.issues else 0
        available = [item.selector for item in project.collect()]
        if args.command == "list":
            scenarios = (
                expand_scenario_paths(available, args.patterns, allow_unmatched=False)
                if args.patterns
                else available
            )
            print("=== AVAILABLE SCENARIOS ===")
            folders: dict[str, list[str]] = {}
            for selector in scenarios:
                folder, _, name = selector.rpartition("/")
                folders.setdefault(folder or "(root)", []).append(name)
            for folder, names in sorted(folders.items()):
                print(
                    f"\n📂 {folder}/" if folder != "(root)" else "\n📂 Root scenarios:"
                )
                for name in sorted(names):
                    print(f"   • {name}")
            print(
                f"\n✨ Total: {len(scenarios)} scenarios available"
                if scenarios
                else "No scenarios found"
            )
            return 0
        preflight = _inventory(project)
        if preflight is not None and preflight.issues:
            raise InventoryError("\n".join(preflight.issues))
        from .reporting import render_terminal_summary, write_run_reports
        from .runner import GenericRunner

        runner = GenericRunner(
            project,
            timeout_seconds=args.timeout,
            cleanup_timeout_seconds=args.cleanup_timeout,
            environment_names=tuple(args.env),
            secret_environment_names=tuple(args.secret_env),
        )
        selectors = expand_scenario_paths(available, args.scenarios)
        if not selectors:
            return 1
        result = runner.run_scenarios(
            scenario_names=selectors, requested_scenarios=list(args.scenarios)
        )
        paths = None
        reporting_error = None
        try:
            paths = write_run_reports(
                result, runner.runs_dir, rerun_prefix=project.rerun_prefix
            )
        except Exception as exc:
            reporting_error = f"Could not write validation reports: {exc}"
        print(
            "\n"
            + render_terminal_summary(
                result,
                show_passed=args.show_passed,
                report_paths=paths,
                reporting_error=reporting_error,
                rerun_prefix=project.rerun_prefix,
            )
        )
        return (
            130
            if result.interrupted
            else (
                1
                if result.failed_scenarios or result.error_scenarios or reporting_error
                else 0
            )
        )
    except (InventoryError, OSError, ValueError) as exc:
        print(f"Validation collection failed: {exc}", file=sys.stderr)
        return 1
