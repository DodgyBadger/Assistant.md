"""AssistantMD command configuration for generic validation reporting."""

from pathlib import Path

from scenario_harness import reporting as generic
from scenario_harness.reporting import ReportPaths

from .results import ValidationRun

__all__ = [
    "ReportPaths",
    "render_markdown_report",
    "render_terminal_summary",
    "rerun_command",
    "write_run_reports",
]

_RERUN_PREFIX = ("uv", "run", "python", "validation/run_validation.py", "run")


def rerun_command(selectors: list[str]) -> str:
    return generic.rerun_command(selectors, rerun_prefix=_RERUN_PREFIX)


def render_terminal_summary(
    validation_run: ValidationRun,
    *,
    show_passed: bool,
    report_paths: ReportPaths | None,
    reporting_error: str | None = None,
) -> str:
    return generic.render_terminal_summary(
        validation_run,
        show_passed=show_passed,
        report_paths=report_paths,
        reporting_error=reporting_error,
        rerun_prefix=_RERUN_PREFIX,
    )


def render_markdown_report(validation_run: ValidationRun) -> str:
    return generic.render_markdown_report(validation_run, rerun_prefix=_RERUN_PREFIX)


def write_run_reports(
    validation_run: ValidationRun, runs_dir: Path, *, retention: int = 20
) -> ReportPaths:
    return generic.write_run_reports(
        validation_run, runs_dir, retention=retention, rerun_prefix=_RERUN_PREFIX
    )
