"""Typed validation run result contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ScenarioStatus = Literal[
    "passed",
    "failed",
    "system_bug",
    "framework_error",
    "error",
]


@dataclass(frozen=True)
class ErrorClassification:
    """Stable classification attached to one non-passing scenario result."""

    type: str
    status: ScenarioStatus
    severity: str
    recommendation: str
    emoji: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-safe classification payload."""
        return {
            "type": self.type,
            "status": self.status,
            "severity": self.severity,
            "recommendation": self.recommendation,
            "emoji": self.emoji,
        }


@dataclass(frozen=True)
class ScenarioResult:
    """Outcome and evidence pointers for one validation scenario."""

    scenario_name: str
    status: ScenarioStatus
    execution_time: float
    error_message: str | None = None
    error_classification: ErrorClassification | None = None
    evidence_path: str | None = None
    stack_trace: str | None = None
    execution_phase: str | None = None
    lifecycle_errors: list[dict[str, str]] = field(default_factory=list)
    timed_out: bool = False
    interrupted: bool = False

    @property
    def passed(self) -> bool:
        """Return whether the scenario completed successfully."""
        return self.status == "passed"

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe scenario payload."""
        return {
            "scenario_name": self.scenario_name,
            "status": self.status,
            "execution_time": self.execution_time,
            "error_message": self.error_message,
            "error_classification": (
                None
                if self.error_classification is None
                else self.error_classification.to_dict()
            ),
            "evidence_path": self.evidence_path,
            "stack_trace": self.stack_trace,
            "execution_phase": self.execution_phase,
            "lifecycle_errors": self.lifecycle_errors,
            "timed_out": self.timed_out,
            "interrupted": self.interrupted,
        }


@dataclass(frozen=True)
class ValidationRun:
    """Aggregate result for one validation CLI invocation."""

    run_id: str
    start_time: datetime
    end_time: datetime
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    error_scenarios: int
    success_rate: float
    scenario_results: list[ScenarioResult] = field(default_factory=list)
    requested_scenarios: list[str] = field(default_factory=list)
    expanded_scenarios: list[str] = field(default_factory=list)
    interrupted: bool = False

    @property
    def non_passing_results(self) -> list[ScenarioResult]:
        """Return failures and errors in execution order."""
        return [result for result in self.scenario_results if not result.passed]

    @property
    def status(self) -> Literal["passed", "failed"]:
        """Return the aggregate run status."""
        return "passed" if not self.non_passing_results else "failed"

    @property
    def failed_selectors(self) -> list[str]:
        """Return selectors suitable for a focused rerun."""
        return [result.scenario_name for result in self.non_passing_results]

    def to_dict(self) -> dict[str, object]:
        """Return the versioned machine-readable report payload."""
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "status": self.status,
            "interrupted": self.interrupted,
            "total_scenarios": self.total_scenarios,
            "passed_scenarios": self.passed_scenarios,
            "failed_scenarios": self.failed_scenarios,
            "error_scenarios": self.error_scenarios,
            "success_rate": self.success_rate,
            "requested_scenarios": list(self.requested_scenarios),
            "expanded_scenarios": list(self.expanded_scenarios),
            "failed_selectors": self.failed_selectors,
            "scenario_results": [result.to_dict() for result in self.scenario_results],
        }
