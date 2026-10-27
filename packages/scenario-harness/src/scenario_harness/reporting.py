"""Terminal and durable validation run reporting."""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .results import ScenarioResult, ValidationRun

_REPORT_RETENTION = 20
_DEFAULT_RERUN_PREFIX = ("scenario-harness", "run")


@dataclass(frozen=True)
class ReportPaths:
    """Paths written for one validation run report."""

    markdown: Path
    json: Path
    latest_markdown: Path
    latest_json: Path


def rerun_command(
    selectors: list[str], *, rerun_prefix: Sequence[str] = _DEFAULT_RERUN_PREFIX
) -> str:
    """Build one copy/paste command for selected scenarios."""
    command = list(rerun_prefix)
    command.extend(selectors)
    return shlex.join(command)


def _status_display(result: ScenarioResult) -> tuple[str, str]:
    displays = {
        "passed": ("✅", "PASSED"),
        "failed": ("❌", "FAILED"),
        "system_bug": ("🚨", "SYSTEM ERROR"),
        "framework_error": ("💥", "FRAMEWORK ERROR"),
        "error": ("❓", "ERROR"),
    }
    return displays[result.status]


def _error_summary(result: ScenarioResult) -> str:
    if not result.error_message:
        return "No error message was captured."
    return result.error_message.splitlines()[0].strip()


def _failure_location(result: ScenarioResult) -> str | None:
    if not result.error_message:
        return None
    prefix = "Failure location: "
    for line in result.error_message.splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    return None


def _timeline_path(result: ScenarioResult) -> Path | None:
    if not result.evidence_path:
        return None
    return Path(result.evidence_path) / "artifacts" / "timeline.md"


def _render_failure(
    result: ScenarioResult, *, rerun_prefix: Sequence[str] = _DEFAULT_RERUN_PREFIX
) -> list[str]:
    symbol, label = _status_display(result)
    lines = [
        f"{symbol} {result.scenario_name} — {label} ({result.execution_time:.2f}s)",
        f"   Cause: {_error_summary(result)}",
    ]
    if result.execution_phase:
        lines.append(f"   Phase: {result.execution_phase}")
    if result.timed_out:
        lines.append("   Timed out: yes")
    if result.interrupted:
        lines.append("   Interrupted: yes")
    for error in result.lifecycle_errors:
        lines.append(
            f"   Lifecycle error ({error.get('phase', 'unknown')}): {error.get('message', '')}"
        )
    location = _failure_location(result)
    if location:
        lines.append(f"   Location: {location}")
    if result.error_classification:
        lines.append(f"   Classification: {result.error_classification.type}")
        lines.append(f"   Recommendation: {result.error_classification.recommendation}")
    if result.evidence_path:
        lines.append(f"   Evidence: {result.evidence_path}")
        timeline = _timeline_path(result)
        if timeline and timeline.exists():
            lines.append(f"   Timeline: {timeline}")
    lines.append(
        f"   Rerun: {rerun_command([result.scenario_name], rerun_prefix=rerun_prefix)}"
    )
    return lines


def render_terminal_summary(
    validation_run: ValidationRun,
    *,
    show_passed: bool,
    report_paths: ReportPaths | None,
    reporting_error: str | None = None,
    rerun_prefix: Sequence[str] = _DEFAULT_RERUN_PREFIX,
) -> str:
    """Render the final failure-focused terminal summary."""
    lines = [
        "=== VALIDATION RUN COMPLETE ===",
        f"Run ID: {validation_run.run_id}",
        f"Total Scenarios: {validation_run.total_scenarios}",
        f"Passed: {validation_run.passed_scenarios}",
        f"Failed: {validation_run.failed_scenarios}",
        f"Errors: {validation_run.error_scenarios}",
        f"Success Rate: {validation_run.success_rate:.1f}%",
    ]
    if report_paths:
        lines.extend(
            [
                f"Markdown report: {report_paths.markdown}",
                f"JSON report: {report_paths.json}",
            ]
        )
    if reporting_error:
        lines.extend(["", "=== REPORTING ERROR ===", reporting_error])

    if show_passed and validation_run.passed_scenarios:
        lines.extend(["", "=== PASSED SCENARIOS ==="])
        for result in validation_run.scenario_results:
            if result.passed:
                lines.append(
                    f"✅ {result.scenario_name} — PASSED ({result.execution_time:.2f}s)"
                )

    failures = validation_run.non_passing_results
    if failures:
        lines.extend(["", f"=== FAILURES AND ERRORS ({len(failures)}) ==="])
        for index, result in enumerate(failures):
            if index:
                lines.append("")
            lines.extend(_render_failure(result, rerun_prefix=rerun_prefix))
        lines.extend(
            [
                "",
                "Rerun all failures:",
                rerun_command(
                    validation_run.failed_selectors, rerun_prefix=rerun_prefix
                ),
            ]
        )
    else:
        lines.extend(["", "✅ All scenarios passed."])
    return "\n".join(lines)


def render_markdown_report(
    validation_run: ValidationRun,
    *,
    rerun_prefix: Sequence[str] = _DEFAULT_RERUN_PREFIX,
) -> str:
    """Render a durable human-readable run report."""
    lines = [
        f"# Validation Run {validation_run.run_id}",
        "",
        f"**Status:** {validation_run.status.upper()}",
        f"**Started:** {validation_run.start_time.isoformat()}",
        f"**Finished:** {validation_run.end_time.isoformat()}",
        "",
    ]
    failures = validation_run.non_passing_results
    if failures:
        lines.extend([f"## Failures and errors ({len(failures)})", ""])
        for result in failures:
            _symbol, label = _status_display(result)
            lines.extend(
                [
                    f"### `{result.scenario_name}` — {label}",
                    "",
                    f"- Cause: {_error_summary(result)}",
                    f"- Duration: {result.execution_time:.2f}s",
                ]
            )
            if result.execution_phase:
                lines.append(f"- Phase: {result.execution_phase}")
            if result.timed_out:
                lines.append("- Timed out: yes")
            if result.interrupted:
                lines.append("- Interrupted: yes")
            for error in result.lifecycle_errors:
                lines.append(
                    f"- Lifecycle error ({error.get('phase', 'unknown')}): {error.get('message', '')}"
                )
            location = _failure_location(result)
            if location:
                lines.append(f"- Location: `{location}`")
            if result.error_classification:
                lines.extend(
                    [
                        f"- Classification: {result.error_classification.type}",
                        f"- Recommendation: {result.error_classification.recommendation}",
                    ]
                )
            if result.evidence_path:
                lines.append(f"- Evidence: `{result.evidence_path}`")
                timeline = _timeline_path(result)
                if timeline and timeline.exists():
                    lines.append(f"- Timeline: `{timeline}`")
            lines.extend(
                [
                    f"- Rerun: `{rerun_command([result.scenario_name], rerun_prefix=rerun_prefix)}`",
                    "",
                ]
            )
        lines.extend(
            [
                "### Rerun all failures",
                "",
                "```bash",
                rerun_command(
                    validation_run.failed_selectors, rerun_prefix=rerun_prefix
                ),
                "```",
                "",
            ]
        )
    else:
        lines.extend(["## Result", "", "All scenarios passed.", ""])

    lines.extend(
        [
            "## Summary",
            "",
            f"- Total: {validation_run.total_scenarios}",
            f"- Passed: {validation_run.passed_scenarios}",
            f"- Failed: {validation_run.failed_scenarios}",
            f"- Errors: {validation_run.error_scenarios}",
            f"- Success rate: {validation_run.success_rate:.1f}%",
            "",
        ]
    )
    if validation_run.passed_scenarios:
        lines.extend(["## Passed scenarios", ""])
        lines.extend(
            f"- `{result.scenario_name}` ({result.execution_time:.2f}s)"
            for result in validation_run.scenario_results
            if result.passed
        )
        lines.append("")
    return "\n".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _prune_reports(reports_dir: Path, *, retention: int) -> None:
    stems = sorted(
        {
            path.stem
            for path in reports_dir.iterdir()
            if path.is_file()
            and path.name not in {"latest.md", "latest.json"}
            and path.suffix in {".md", ".json"}
        },
        reverse=True,
    )
    for stem in stems[retention:]:
        for suffix in (".md", ".json"):
            path = reports_dir / f"{stem}{suffix}"
            if path.exists():
                path.unlink()


def write_run_reports(
    validation_run: ValidationRun,
    runs_dir: Path,
    *,
    retention: int = _REPORT_RETENTION,
    rerun_prefix: Sequence[str] = _DEFAULT_RERUN_PREFIX,
) -> ReportPaths:
    """Atomically write timestamped and latest Markdown/JSON run reports."""
    reports_dir = runs_dir / "reports"
    markdown = reports_dir / f"{validation_run.run_id}.md"
    json_path = reports_dir / f"{validation_run.run_id}.json"
    latest_markdown = reports_dir / "latest.md"
    latest_json = reports_dir / "latest.json"

    markdown_content = render_markdown_report(validation_run, rerun_prefix=rerun_prefix)
    json_content = json.dumps(validation_run.to_dict(), indent=2, sort_keys=True)
    json_content += "\n"
    _atomic_write(markdown, markdown_content)
    _atomic_write(json_path, json_content)
    _atomic_write(latest_markdown, markdown_content)
    _atomic_write(latest_json, json_content)
    _prune_reports(reports_dir, retention=retention)
    return ReportPaths(
        markdown=markdown,
        json=json_path,
        latest_markdown=latest_markdown,
        latest_json=latest_json,
    )
