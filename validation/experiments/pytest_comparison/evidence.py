"""Small pytest reporting extension; pytest owns collection and execution."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_STATE: pytest.StashKey[RunEvidence] = pytest.StashKey()


@dataclass
class Evidence:
    root: Path
    secrets: list[str] = field(default_factory=list, repr=False)
    _lock: Any = field(default_factory=threading.RLock, repr=False)

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {str(key): self.redact(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.redact(item) for item in value]
        return value

    def append(self, name: str, value: Any) -> None:
        with self._lock:
            with (self.artifacts / name).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(self.redact(value), default=str) + "\n")

    def save(self, name: str, value: Any) -> None:
        with self._lock:
            (self.artifacts / name).write_text(
                json.dumps(self.redact(value), indent=2, default=str) + "\n",
                encoding="utf-8",
            )

    def step(self, name: str, **data: Any) -> None:
        self.append(
            "timeline.jsonl",
            {"time": datetime.now(UTC).isoformat(), "step": name, **data},
        )


@dataclass
class RunEvidence:
    root: Path
    items: dict[str, Evidence] = field(default_factory=dict)
    reports: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def evidence(self, nodeid: str) -> Evidence:
        if nodeid not in self.items:
            label = re.sub(r"[^A-Za-z0-9_-]", "_", nodeid.split("::")[-1])
            digest = hashlib.sha256(nodeid.encode()).hexdigest()[:10]
            item = Evidence(self.root / f"{label}-{digest}")
            item.artifacts.mkdir(parents=True)
            self.items[nodeid] = item
        return self.items[nodeid]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--evidence-root", help="Empty output directory for this comparison run"
    )


def pytest_configure(config: pytest.Config) -> None:
    if not config.getoption("file_or_dir"):
        raise pytest.UsageError(
            "Provide an explicit comparison test file; this experiment never falls back to repository-wide discovery"
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    configured = session.config.getoption("evidence_root")
    root = (
        Path(configured).resolve()
        if configured
        else Path(tempfile.mkdtemp(prefix="pytest-comparison-"))
    )
    if root.exists() and any(root.iterdir()):
        raise pytest.UsageError("--evidence-root must be empty")
    root.mkdir(parents=True, exist_ok=True)
    session.config.stash[_STATE] = RunEvidence(root)


def pytest_collection_modifyitems(
    session: pytest.Session, items: list[pytest.Item]
) -> None:
    for item in items:
        session.config.stash[_STATE].evidence(item.nodeid)


@pytest.fixture
def evidence(request: pytest.FixtureRequest) -> Evidence:
    return request.config.stash[_STATE].evidence(request.node.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[Any]
) -> Generator[None, Any]:
    outcome = yield
    report = outcome.get_result()
    state = item.config.stash[_STATE]
    evidence = state.evidence(item.nodeid)
    phase = {
        "phase": report.when,
        "outcome": report.outcome,
        "duration": report.duration,
        "traceback": str(report.longrepr) if report.longrepr else None,
        "captures": [
            {"name": name, "text": content} for name, content in report.sections
        ],
    }
    state.reports.setdefault(item.nodeid, []).append(evidence.redact(phase))
    evidence.save("pytest-phases.json", state.reports[item.nodeid])
    evidence.step("pytest_phase", phase=report.when, outcome=report.outcome)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = session.config.stash[_STATE]
    tests = []
    lines = ["# Pytest scenario comparison", "", f"Exit status: {int(exitstatus)}", ""]
    for nodeid, evidence in state.items.items():
        phases = state.reports.get(nodeid, [])
        status = (
            "failed"
            if any(p["outcome"] == "failed" for p in phases)
            else (
                "passed"
                if any(
                    p["phase"] == "call" and p["outcome"] == "passed" for p in phases
                )
                and any(
                    p["phase"] == "teardown" and p["outcome"] == "passed"
                    for p in phases
                )
                else "incomplete"
            )
        )
        tests.append(
            {
                "nodeid": nodeid,
                "status": status,
                "evidence_path": str(evidence.root),
                "phases": phases,
            }
        )
        lines.extend(
            [
                f"## {nodeid}",
                "",
                f"Result: **{status}**",
                f"Evidence: [{evidence.root.name}]({evidence.root.name}/artifacts/)",
                "",
            ]
        )
        for phase in phases:
            lines.append(
                f"- {phase['phase']}: {phase['outcome']} ({phase['duration']:.3f}s)"
            )
            if phase["traceback"]:
                lines.extend(["", "```text", phase["traceback"], "```", ""])
        lines.append("")
    payload = {"schema_version": 1, "exit_status": int(exitstatus), "tests": tests}
    (state.root / "report.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (state.root / "report.md").write_text("\n".join(lines), encoding="utf-8")


def pytest_terminal_summary(terminalreporter: Any) -> None:
    root = terminalreporter.config.stash[_STATE].root
    terminalreporter.write_sep("=", f"Scenario evidence: {root / 'report.md'}")
