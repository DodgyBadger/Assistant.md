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
    is_worker: bool = False
    external_paths: dict[str, str] = field(default_factory=dict)
    worker_events: list[dict[str, Any]] = field(default_factory=list)

    def worker_event(self, event: str, worker: str, **details: Any) -> None:
        record = {"event": event, "worker": worker, **details}
        self.worker_events.append(record)
        with (self.root / "worker-events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, default=str) + "\n")

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
    config.pluginmanager.register(
        ReportCollector(config), "comparison-report-collector"
    )
    if not config.getoption("file_or_dir"):
        raise pytest.UsageError(
            "Provide an explicit comparison test file; this experiment never falls back to repository-wide discovery"
        )


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session: pytest.Session) -> None:
    worker_input = getattr(session.config, "workerinput", None)
    if worker_input is not None:
        root = (
            Path(worker_input["comparison_evidence_root"])
            / "workers"
            / worker_input["workerid"]
        )
        root.mkdir(parents=True)
        session.config.stash[_STATE] = RunEvidence(root, is_worker=True)
        return
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


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node: Any) -> None:
    state = node.config.stash[_STATE]
    node.workerinput["comparison_evidence_root"] = str(state.root)
    state.worker_event("worker_created", node.gateway.id)


@pytest.hookimpl(optionalhook=True)
def pytest_xdist_node_collection_finished(node: Any, ids: list[str]) -> None:
    state = node.config.stash[_STATE]
    for nodeid in ids:
        state.evidence(nodeid)


@pytest.hookimpl(optionalhook=True)
def pytest_testnodedown(node: Any, error: Any) -> None:
    node.config.stash[_STATE].worker_event(
        "worker_down", node.gateway.id, error=str(error) if error else None
    )


@pytest.hookimpl(optionalhook=True)
def pytest_handlecrashitem(crashitem: str, report: pytest.TestReport) -> None:
    report.user_properties.append(("comparison_worker_crash", True))


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
        "exception": (
            {
                "type": f"{type(call.excinfo.value).__module__}.{type(call.excinfo.value).__qualname__}",
                "summary": call.excinfo.exconly(),
            }
            if call.excinfo is not None
            else None
        ),
        "phase": report.when,
        "outcome": report.outcome,
        "duration": report.duration,
        "traceback": str(report.longrepr) if report.longrepr else None,
        "captures": [
            {"name": name, "text": content} for name, content in report.sections
        ],
    }
    report.user_properties.extend(
        [
            ("comparison_phase", evidence.redact(phase)),
            ("comparison_evidence_path", str(evidence.root)),
        ]
    )


class ReportCollector:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        state = self.config.stash[_STATE]
        evidence = state.evidence(report.nodeid)
        properties = dict(report.user_properties)
        path = properties.get("comparison_evidence_path")
        if isinstance(path, str):
            state.external_paths[report.nodeid] = path
        transported_phase = properties.get("comparison_phase")
        phase: dict[str, Any]
        if isinstance(transported_phase, dict):
            phase = dict(transported_phase)
        else:
            phase = {
                "exception": None,
                "phase": (
                    "worker"
                    if properties.get("comparison_worker_crash")
                    else report.when
                ),
                "outcome": report.outcome,
                "duration": report.duration,
                "traceback": str(report.longrepr) if report.longrepr else None,
                "captures": [
                    {"name": name, "text": text} for name, text in report.sections
                ],
            }
        phase = {
            **phase,
            "worker_crash": bool(properties.get("comparison_worker_crash")),
        }
        state.reports.setdefault(report.nodeid, []).append(evidence.redact(phase))
        evidence.save("pytest-phases.json", state.reports[report.nodeid])
        evidence.step("pytest_phase", phase=phase["phase"], outcome=phase["outcome"])


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = session.config.stash[_STATE]
    if state.is_worker:
        return
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
        crashed = any(phase.get("worker_crash") for phase in phases)
        evidence_path = state.external_paths.get(nodeid, str(evidence.root))
        fatal_stack = Path(evidence_path) / "artifacts/fatal-stack.txt"
        cleanup_status = (
            "unconfirmed"
            if crashed
            else (
                "completed"
                if any(
                    phase["phase"] == "teardown" and phase["outcome"] == "passed"
                    for phase in phases
                )
                else "failed_or_incomplete"
            )
        )
        tests.append(
            {
                "nodeid": nodeid,
                "status": status,
                "evidence_path": evidence_path,
                "controller_evidence_path": str(evidence.root),
                "worker_crashed": crashed,
                "cleanup_status": cleanup_status,
                "fatal_stack_path": str(fatal_stack) if fatal_stack.exists() else None,
                "phases": phases,
            }
        )
        lines.extend(
            [
                f"## {nodeid}",
                "",
                f"Result: **{status}**",
                f"Cleanup: **{cleanup_status}**",
                f"Evidence: [{Path(evidence_path).name}]({Path(evidence_path).relative_to(state.root).as_posix()}/artifacts/)",
                "",
            ]
        )
        if fatal_stack.exists():
            lines.extend(
                [
                    f"Fatal-signal stack: [fatal-stack.txt]({fatal_stack.relative_to(state.root).as_posix()})",
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
    payload = {
        "schema_version": 1,
        "exit_status": int(exitstatus),
        "tests": tests,
        "worker_events": state.worker_events,
    }
    (state.root / "report.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    (state.root / "report.md").write_text("\n".join(lines), encoding="utf-8")


def pytest_terminal_summary(terminalreporter: Any) -> None:
    state = terminalreporter.config.stash[_STATE]
    if state.is_worker:
        return
    root = state.root
    terminalreporter.write_sep("=", f"Scenario evidence: {root / 'report.md'}")
