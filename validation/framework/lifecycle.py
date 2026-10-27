"""Bounded, observable lifecycle for an application validation run."""

from __future__ import annotations

import asyncio
import inspect
import json
import traceback
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

Callback = Callable[[], Awaitable[None] | None]
_current_context: ContextVar[RunContext | None] = ContextVar(
    "validation_run_context", default=None
)


@dataclass(frozen=True)
class PhaseError:
    name: str
    phase: str
    exception_type: str
    message: str
    traceback: str


@dataclass
class ExecutionOutcome:
    primary_error: BaseException | None = field(default=None, repr=False)
    primary_phase: str | None = None
    cleanup_errors: list[PhaseError] = field(default_factory=list)
    evidence_errors: list[PhaseError] = field(default_factory=list)
    cancelled: bool = False
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return not (
            self.primary_error is not None
            or self.cleanup_errors
            or self.evidence_errors
            or self.cancelled
            or self.timed_out
        )


class RunContext:
    def __init__(self, root: Path, secrets: Mapping[str, str] | None = None):
        self.application: object | None = None
        self.root = Path(root)
        self.artifacts_dir = self.root / "artifacts"
        self.events_path = self.artifacts_dir / "events.jsonl"
        self._secret_inputs = dict(secrets or {})
        self._secrets: tuple[str, ...] = ()
        for value in self._secret_inputs.values():
            self.register_secret(value)
        self._cleanups: list[tuple[str, Callback]] = []
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._events = self.events_path.open("a", encoding="utf-8")

    def __repr__(self) -> str:
        return f"RunContext(root={self._redact(str(self.root))!r})"

    def _redact(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, Mapping):
            return {self._redact(str(k)): self._redact(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [self._redact(item) for item in value]
        if value is None or isinstance(value, bool | int | float):
            return value
        return self._redact(str(value))

    def redact(self, value: Any) -> Any:
        """Return a recursively redacted representation suitable for evidence."""
        return self._redact(value)

    def secret(self, name: str) -> str:
        return self._secret_inputs[name]

    def register_secret(self, value: str) -> None:
        if value and value not in self._secrets:
            self._secrets = tuple(
                sorted((*self._secrets, value), key=len, reverse=True)
            )

    def emit(self, event: str, **data: Any) -> None:
        record = self._redact(
            {"timestamp": datetime.now(UTC).isoformat(), "event": event, **data}
        )
        self._events.write(json.dumps(record, allow_nan=False) + "\n")
        self._events.flush()

    def defer_cleanup(self, name: str, callback: Callback) -> None:
        self._cleanups.append((name, callback))

    def close(self) -> None:
        self._events.close()


class ApplicationAdapter(Protocol):
    async def setup(self, context: RunContext) -> None: ...
    async def teardown(self, context: RunContext) -> None: ...


def current_run_context() -> RunContext | None:
    return _current_context.get()


def _phase_error(
    context: RunContext, name: str, phase: str, exc: BaseException
) -> PhaseError:
    return PhaseError(
        name=context._redact(name),
        phase=phase,
        exception_type=type(exc).__name__,
        message=context._redact(str(exc)),
        traceback=context._redact("".join(traceback.format_exception(exc))),
    )


async def _invoke(callback: Callback) -> None:
    result = callback()
    if inspect.isawaitable(result):
        await result


async def _execute(
    context: RunContext,
    adapter: ApplicationAdapter,
    body: Callback,
    outcome: ExecutionOutcome,
    timeout_seconds: float | None = None,
    cleanup_timeout_seconds: float = 30,
) -> ExecutionOutcome:
    phase = "setup"

    def journal(event: str, **data: Any) -> None:
        try:
            context.emit(event, **data)
        except BaseException as exc:
            outcome.evidence_errors.append(
                _phase_error(context, event, "evidence", exc)
            )

    async def work() -> None:
        nonlocal phase
        journal("setup_started")
        await adapter.setup(context)
        journal("setup_completed")
        phase = "execution"
        journal("execution_started")
        await _invoke(body)
        journal("execution_completed")

    context.defer_cleanup("adapter.teardown", lambda: adapter.teardown(context))
    journal("run_started")
    try:
        async with asyncio.timeout(timeout_seconds):
            await work()
    except BaseException as exc:
        outcome.primary_error = exc
        outcome.primary_phase = phase
        outcome.cancelled = isinstance(exc, asyncio.CancelledError | KeyboardInterrupt)
        outcome.timed_out = isinstance(exc, TimeoutError)
        journal(
            f"{phase}_failed", error=_phase_error(context, phase, phase, exc).__dict__
        )

    journal("cleanup_started")
    deadline = asyncio.get_running_loop().time() + max(0.0, cleanup_timeout_seconds)
    while context._cleanups:
        name, callback = context._cleanups.pop()
        journal("cleanup_callback_started", name=name)
        try:
            remaining = deadline - asyncio.get_running_loop().time()
            async with asyncio.timeout(max(0.0, remaining)):
                await _invoke(callback)
        except BaseException as exc:
            if isinstance(exc, asyncio.CancelledError | KeyboardInterrupt):
                outcome.cancelled = True
            error = _phase_error(context, name, "cleanup", exc)
            outcome.cleanup_errors.append(error)
            journal("cleanup_callback_failed", error=error.__dict__)
        else:
            journal("cleanup_callback_completed", name=name)
    journal("cleanup_completed", errors=len(outcome.cleanup_errors))
    journal(
        "run_completed",
        passed=outcome.passed,
        cancelled=outcome.cancelled,
        timed_out=outcome.timed_out,
        primary_phase=outcome.primary_phase,
        cleanup_errors=len(outcome.cleanup_errors),
        evidence_errors=len(outcome.evidence_errors),
    )
    return outcome


async def execute(
    context: RunContext,
    adapter: ApplicationAdapter,
    body: Callback,
    timeout_seconds: float | None = None,
    cleanup_timeout_seconds: float = 30,
) -> ExecutionOutcome:
    token = _current_context.set(context)
    outcome = ExecutionOutcome()
    try:
        return await _execute(
            context, adapter, body, outcome, timeout_seconds, cleanup_timeout_seconds
        )
    finally:
        try:
            try:
                context.close()
            except BaseException as exc:
                outcome.evidence_errors.append(
                    _phase_error(context, "close", "evidence", exc)
                )
        finally:
            _current_context.reset(token)
