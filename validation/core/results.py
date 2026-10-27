"""Typed validation run result contracts."""

from __future__ import annotations

import math
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

    @classmethod
    def from_dict(cls, value: object) -> ScenarioResult:
        """Decode the worker protocol, rejecting malformed terminal outcomes."""
        if not isinstance(value, dict):
            raise ValueError("Worker result must be an object")
        if value.get("status") not in {
            "passed",
            "failed",
            "system_bug",
            "framework_error",
            "error",
        }:
            raise ValueError("Invalid worker status")
        for name in ("scenario_name", "evidence_path"):
            if not isinstance(value.get(name), str):
                raise ValueError(f"Invalid worker {name}")
        duration = value.get("execution_time")
        if (
            not isinstance(duration, int | float)
            or isinstance(duration, bool)
            or not math.isfinite(duration)
            or duration < 0
        ):
            raise ValueError("Invalid worker duration")
        for name in ("error_message", "stack_trace", "execution_phase"):
            if value.get(name) is not None and not isinstance(value[name], str):
                raise ValueError(f"Invalid worker {name}")
        for name in ("timed_out", "interrupted"):
            if type(value.get(name)) is not bool:
                raise ValueError(f"Invalid worker {name}")
        errors = value.get("lifecycle_errors")
        if not isinstance(errors, list) or any(
            not isinstance(error, dict)
            or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in error.items()
            )
            for error in errors
        ):
            raise ValueError("Invalid worker lifecycle errors")
        classification = value.get("error_classification")
        if classification is not None:
            if not isinstance(classification, dict) or any(
                not isinstance(item, str) for item in classification.values()
            ):
                raise ValueError("Invalid worker classification")
            classification = ErrorClassification(**classification)
            if classification.status != value["status"]:
                raise ValueError("Worker classification status mismatch")
        if value["status"] == "passed" and (
            errors
            or value["timed_out"]
            or value["interrupted"]
            or value.get("error_message")
        ):
            raise ValueError("Contradictory worker success")
        return cls(**{**value, "error_classification": classification})

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
