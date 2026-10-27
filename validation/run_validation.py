#!/usr/bin/env python3
"""
CLI entry point for validation execution.

Provides command-line interface for running validation scenarios
with enhanced evidence collection and user-focused reporting.
"""

import argparse
import fnmatch
import json
import logging
import sys
from pathlib import Path

# Add project root to path FIRST
sys.path.insert(0, str(Path(__file__).parent.parent))

from validation.core.inventory import InventoryError, ScenarioCatalog, build_inventory
from validation.core.paths import resolve_validation_root

logger = logging.getLogger("validation-cli")


def expand_scenario_paths(runner, scenario_specs, *, allow_unmatched: bool = True):
    """
    Expand scenario specifications to full scenario names.

    Supports:
    - Individual scenarios: "basic_haiku" or "integration/basic_haiku"
    - Folders: "integration" expands to all scenarios in that folder
    - Globs: "integration/context_manager*" expands by fnmatch pattern
    """
    expanded = []
    all_scenarios = runner.discover_scenarios()

    for spec in scenario_specs:
        # Normalize path separators
        spec = spec.replace("\\", "/")

        if any(ch in spec for ch in "*?[]"):
            glob_matches = [s for s in all_scenarios if fnmatch.fnmatchcase(s, spec)]
            if glob_matches:
                expanded.extend(sorted(glob_matches))
                logger.info(
                    f"Expanded pattern '{spec}' to {len(glob_matches)} scenarios"
                )
                continue
            logger.warning(f"No scenarios found matching pattern '{spec}'")
            if allow_unmatched:
                expanded.append(spec)
            continue

        # Check if this is an exact match for a scenario
        if spec in all_scenarios:
            expanded.append(spec)
            continue

        # Check if this is a folder containing scenarios
        folder_matches = [s for s in all_scenarios if s.startswith(f"{spec}/")]
        if folder_matches:
            expanded.extend(folder_matches)
            logger.info(f"Expanded folder '{spec}' to {len(folder_matches)} scenarios")
            continue

        # Not found - let it through and fail during execution with clear error
        logger.warning(f"No scenarios found matching '{spec}'")
        if allow_unmatched:
            expanded.append(spec)

    return expanded


def run_scenarios(args):
    """Run validation scenarios."""
    # A temporary evidence root may link to checkout-owned scenarios/governance.
    source_root = (resolve_validation_root() / "scenarios").resolve().parent
    preflight = build_inventory(source_root)
    if preflight.issues:
        raise InventoryError("\n".join(preflight.issues))

    from validation.core.reporting import render_terminal_summary, write_run_reports
    from validation.core.runner import ValidationRunner

    runner = ValidationRunner(
        timeout_seconds=args.timeout,
        cleanup_timeout_seconds=args.cleanup_timeout,
        environment_names=tuple(args.env),
        secret_environment_names=tuple(args.secret_env),
    )

    # Expand folder paths to individual scenarios
    scenario_names = expand_scenario_paths(runner, args.scenarios, allow_unmatched=True)

    if not scenario_names:
        logger.error("No scenarios to run")
        sys.exit(1)

    # Run scenarios
    validation_run = runner.run_scenarios(
        scenario_names=scenario_names,
        requested_scenarios=list(args.scenarios),
    )

    report_paths = None
    reporting_error = None
    try:
        report_paths = write_run_reports(validation_run, runner.runs_dir)
    except Exception as exc:
        reporting_error = f"Could not write validation reports: {exc}"
        logger.error(reporting_error)

    print(
        "\n"
        + render_terminal_summary(
            validation_run,
            show_passed=args.show_passed,
            report_paths=report_paths,
            reporting_error=reporting_error,
        )
    )

    # Exit with appropriate code
    sys.exit(
        130
        if validation_run.interrupted
        else (
            0
            if validation_run.failed_scenarios == 0
            and validation_run.error_scenarios == 0
            and reporting_error is None
            else 1
        )
    )


def list_scenarios(args):
    """List available scenarios."""
    runner = ScenarioCatalog(resolve_validation_root())
    if args.patterns:
        scenarios = expand_scenario_paths(runner, args.patterns, allow_unmatched=False)
    else:
        scenarios = runner.discover_scenarios()

    print("=== AVAILABLE SCENARIOS ===")
    if scenarios:
        # Group scenarios by folder
        folders = {}
        for scenario in scenarios:
            if "/" in scenario:
                folder = scenario.rsplit("/", 1)[0]
                scenario_name = scenario.rsplit("/", 1)[1]
            else:
                folder = "(root)"
                scenario_name = scenario

            if folder not in folders:
                folders[folder] = []
            folders[folder].append((scenario, scenario_name))

        # Print grouped by folder
        for folder in sorted(folders.keys()):
            if folder == "(root)":
                print("\n📂 Root scenarios:")
            else:
                print(f"\n📂 {folder}/")

            for _full_path, name in sorted(folders[folder], key=lambda x: x[1]):
                print(f"   • {name}")

        print(f"\n✨ Total: {len(scenarios)} scenarios available")
        print("\n💡 Usage:")
        print(
            "   python validation/run_validation.py run basic_haiku          # Run single scenario"
        )
        print(
            "   python validation/run_validation.py run integration          # Run all scenarios in folder"
        )
        print(
            "   python validation/run_validation.py run integration experimental  # Run multiple folders"
        )

    else:
        print("No scenarios found")
        print("\n🚀 To create a scenario:")
        print("   1. Add a .py file to validation/scenarios/ (or subfolder)")
        print("   2. Create a class that inherits from BaseScenario")
        print("   3. Implement the test_scenario() method")


def inventory(args):
    """Check structural accounting without executing or importing the app."""
    root = Path(args.root) if args.root else resolve_validation_root()
    report = build_inventory(root)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"Scenarios: {len(report.scenarios)}")
        print(
            f"Standalone opt-in experiment scripts: {len(report.standalone_experiments)}"
        )
        print(
            f"Legacy standalone suites: {report.legacy_test_files} files, {report.legacy_named_tests} named tests"
        )
        for location, count in report.legacy_locations.items():
            print(f"  {location}: {count} files")
        print(
            "Legacy accounting is not execution or proof of complete behavioral coverage."
        )
        print("Structural inventory: " + ("FAILED" if report.issues else "complete"))
        for issue in report.issues:
            print(f"- {issue}")
    sys.exit(1 if report.issues else 0)


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Run validation scenarios for the AssistantMD",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Validation Framework - User-focused scenario testing

Examples:
      python validation/run_validation.py run                    # Run all scenarios
  python validation/run_validation.py run weekly_planning daily_journaling
  python validation/run_validation.py list                        # List available scenarios

Features:
  ✅ Real assistant files in scenario folders
  ✅ High-level, readable scenario code
  ✅ Comprehensive evidence collection
  ✅ User workflow focus vs feature testing
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run validation scenarios")
    run_parser.add_argument(
        "scenarios",
        nargs="+",
        help="One or more scenario names to run",
    )
    run_parser.add_argument(
        "--show-passed",
        action="store_true",
        help="Include individual passing scenarios in the final terminal summary",
    )

    # List command
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="Setup/execution budget in seconds (default: 300); process bound also includes cleanup budget",
    )
    run_parser.add_argument(
        "--cleanup-timeout",
        type=float,
        default=30,
        help="Async cleanup deadline in seconds",
    )
    run_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="NAME",
        help="Pass a named non-secret environment variable to scenario workers",
    )
    run_parser.add_argument(
        "--secret-env",
        action="append",
        default=[],
        metavar="NAME",
        help="Pass a named secret environment variable and register its value for redaction",
    )

    list_parser = subparsers.add_parser("list", help="List available scenarios")
    list_parser.add_argument(
        "patterns",
        nargs="*",
        help="Optional glob patterns to filter scenarios",
    )

    inventory_parser = subparsers.add_parser(
        "inventory",
        help="Check scenario and legacy-test inventory without executing tests",
    )
    inventory_parser.add_argument("--root", help="Validation directory to inspect")
    inventory_parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable structural inventory",
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Run the appropriate command
    try:
        if args.command == "run":
            run_scenarios(args)
        elif args.command == "list":
            list_scenarios(args)
        elif args.command == "inventory":
            inventory(args)
    except InventoryError as exc:
        print(f"Validation collection failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
