"""Unified logger providing technical instrumentation and activity logging."""

from __future__ import annotations

import fcntl
import hashlib
import inspect
import json
import logging
import os
import time
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from threading import Lock
from types import ModuleType
from typing import Any, Literal, cast

import logfire
import yaml
from fastapi import FastAPI
from logfire.sampling import SamplingOptions
from opentelemetry.context import Context
from opentelemetry.sdk.trace.sampling import (
    ALWAYS_ON,
    Decision,
    Sampler,
    SamplingResult,
)
from opentelemetry.trace import Link, SpanKind, TraceState
from opentelemetry.util.types import AttributeValue

from core.activity_log import (
    DEFAULT_MAX_TOTAL_BYTES,
    DEFAULT_RETENTION_DAYS,
    prune_activity_segments,
)
from core.runtime import state as runtime_state
from core.runtime.paths import get_system_root
from core.settings.secrets_store import get_secret_value
from core.settings.store import get_general_settings

_activity_logger: logging.Logger | None = None
_activity_log_path: Path | None = None
_activity_logger_lock = Lock()
_validation_log_lock = Lock()
_warning_dedupe_lock = Lock()
_validation_event_counter = 0
_validation_boot_id: int | None = None
_warning_dedupe_boot_id: int | None = None
_warning_dedupe_keys: set[tuple[str, str, str | None]] = set()
_logfire_config_state: tuple[bool, str | None] | None = None
_logfire_instrumented = False
_logger_internal = logging.getLogger(__name__)
_NOISY_LOGFIRE_MESSAGES = frozenset(
    {
        "Pydantic nullable validate_python",
        "Pydantic union validate_python",
    }
)
_NOISY_PYDANTIC_SCHEMAS = frozenset({"nullable", "union"})
ACTIVITY_LOG_RETENTION_DAYS = DEFAULT_RETENTION_DAYS
ACTIVITY_LOG_MAX_TOTAL_BYTES = DEFAULT_MAX_TOTAL_BYTES


class _BoundedTimedActivityHandler(TimedRotatingFileHandler):
    """Serialize daily rollover and appends across application processes."""

    def __init__(self, filename: Path, **kwargs: Any) -> None:
        self._lock_path = Path(f"{filename}.lock")
        super().__init__(filename, **kwargs)

    def emit(self, record: logging.LogRecord) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                self._reopen_if_rotated()
                if self.shouldRollover(record):
                    self.doRollover()
                logging.FileHandler.emit(self, record)
                self.flush()
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _reopen_if_rotated(self) -> None:
        active_path = Path(self.baseFilename)
        if self.stream is None or not active_path.exists():
            return
        try:
            stream_stat = os.fstat(self.stream.fileno())
            active_stat = active_path.stat()
        except (OSError, ValueError):
            return
        if (stream_stat.st_dev, stream_stat.st_ino) == (
            active_stat.st_dev,
            active_stat.st_ino,
        ):
            return
        self.stream.close()
        self.stream = self._open()
        self.rolloverAt = self.computeRollover(int(time.time()))

    def doRollover(self) -> None:
        super().doRollover()
        self._prune_total_size()

    def _prune_total_size(self) -> None:
        prune_activity_segments(
            Path(self.baseFilename),
            retention_days=ACTIVITY_LOG_RETENTION_DAYS,
            max_total_bytes=ACTIVITY_LOG_MAX_TOTAL_BYTES,
        )

    def prune_now(self) -> None:
        """Run startup pruning under the same inter-process writer lock."""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                self._prune_total_size()
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


class _LogfireNoiseFilteringSampler(Sampler):
    """Drop high-volume dependency spans before console or remote export."""

    def __init__(self, delegate: Sampler = ALWAYS_ON) -> None:
        self._delegate = delegate

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Mapping[str, AttributeValue] | None = None,
        links: Sequence[Link] | None = None,
        trace_state: TraceState | None = None,
    ) -> SamplingResult:
        span_message = ""
        schema_name = ""
        validation_method = ""
        if attributes:
            span_message = str(attributes.get("logfire.msg") or "").strip()
            schema_name = str(attributes.get("schema_name") or "").strip()
            validation_method = str(attributes.get("validation_method") or "").strip()
        if span_message in _NOISY_LOGFIRE_MESSAGES:
            return SamplingResult(Decision.DROP, trace_state=trace_state)
        if (
            str(name).strip() == "pydantic.validate_python"
            and schema_name in _NOISY_PYDANTIC_SCHEMAS
            and validation_method == "validate_python"
        ):
            return SamplingResult(Decision.DROP, trace_state=trace_state)
        return self._delegate.should_sample(
            parent_context=parent_context,
            trace_id=trace_id,
            name=name,
            kind=kind,
            attributes=attributes,
            links=links,
            trace_state=trace_state,
        )

    def get_description(self) -> str:
        return "LogfireNoiseFilteringSampler"


def _token_fingerprint(token: str | None) -> str | None:
    """Create a stable fingerprint for secret comparison without storing raw values."""
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_logfire_configuration(force: bool = False) -> None:
    """
    Reconfigure the global Logfire client based on current settings and secrets.

    Args:
        force: When True, always reapply configuration even if nothing changed.
    """
    global _logfire_config_state

    enabled = False
    token = None

    try:
        settings = get_general_settings()
        entry = settings.get("logfire")
        enabled = bool(entry and getattr(entry, "value", False))
        token = get_secret_value("LOGFIRE_TOKEN")
    except Exception as exc:  # pragma: no cover - defensive guard
        _logger_internal.warning("Logfire configuration deferred: %s", exc)

    fingerprint = _token_fingerprint(token)
    desired_state = (enabled, fingerprint)

    # Keep environment token synchronized for downstream libraries.
    if token:
        os.environ["LOGFIRE_TOKEN"] = token
    else:
        os.environ.pop("LOGFIRE_TOKEN", None)

    if not force and _logfire_config_state == desired_state:
        return

    send_option: Literal["if-token-present"] | bool = (
        "if-token-present" if enabled else False
    )

    logfire.configure(
        send_to_logfire=send_option,
        sampling=SamplingOptions(head=_LogfireNoiseFilteringSampler()),
        scrubbing=False,
    )

    _logfire_config_state = desired_state


def _ensure_activity_logger() -> logging.Logger:
    """Create or return the process-wide activity logger."""

    global _activity_logger
    global _activity_log_path

    desired_path = _resolve_activity_log_path()

    if _activity_logger and _activity_log_path == desired_path:
        return _activity_logger

    with _activity_logger_lock:
        if _activity_logger and _activity_log_path == desired_path:
            return _activity_logger

        # Tear down existing logger if the target path changes between runs
        if _activity_logger and _activity_log_path != desired_path:
            for handler in list(_activity_logger.handlers):
                _activity_logger.removeHandler(handler)
                handler.close()
            _activity_logger = None

        log_path = desired_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        handler = _BoundedTimedActivityHandler(
            log_path,
            when="midnight",
            interval=1,
            backupCount=ACTIVITY_LOG_RETENTION_DAYS,
            utc=True,
            encoding="utf-8",
        )
        handler.prune_now()
        handler.setFormatter(logging.Formatter("%(message)s"))

        logger = logging.getLogger("project_assistant.activity")
        logger.setLevel(logging.INFO)
        logger.propagate = False

        # Avoid duplicate handlers if logger already configured externally
        if not logger.handlers:
            logger.addHandler(handler)

        _activity_logger = logger
        _activity_log_path = log_path
        return logger


def _resolve_activity_log_path() -> Path:
    """Determine the correct activity log path based on the active runtime context."""
    try:
        return Path(get_system_root()) / "activity.log"
    except Exception:
        # Last-resort fallback if path resolution fails unexpectedly
        return Path("/app/system") / "activity.log"


def _validation_features() -> dict[str, Any]:
    """Return validation feature flags from the runtime context, if available."""
    if not runtime_state.has_runtime_context():
        return {}
    try:
        runtime = runtime_state.get_runtime_context()
    except Exception:
        return {}
    return runtime.config.features or {}


def _validation_enabled() -> bool:
    """Return True when the runtime is in validation mode."""
    return bool(_validation_features().get("validation"))


def _warnings_deduped() -> bool:
    """Return True when warning deduplication is enabled."""
    features = _validation_features()
    return bool(features.get("dedupe_warnings", True))


def _resolve_validation_artifact_dir() -> Path | None:
    """Resolve the validation artifact directory for validation-only logging."""
    if not _validation_enabled():
        return None

    features = _validation_features()
    artifacts_dir = features.get("validation_artifacts_dir")
    if artifacts_dir:
        try:
            return Path(artifacts_dir) / "validation_events"
        except (TypeError, ValueError):
            pass

    try:
        return Path(get_system_root()).parent / "artifacts" / "validation_events"
    except Exception:
        return None


def _write_validation_record(record: dict[str, Any]) -> None:
    """Write a validation artifact record to a YAML file."""
    directory = _resolve_validation_artifact_dir()
    if directory is None:
        return

    with _validation_log_lock:
        global _validation_event_counter
        global _validation_boot_id
        boot_id = _get_runtime_boot_id()
        if boot_id is not None and boot_id != _validation_boot_id:
            _validation_event_counter = 0
            _validation_boot_id = boot_id
        _validation_event_counter += 1
        event_id = _validation_event_counter

        directory.mkdir(parents=True, exist_ok=True)
        tag = _sanitize_validation_name(record.get("tag", "event"))
        name = _sanitize_validation_name(record.get("name", "event"))
        if boot_id is not None:
            filename = f"{boot_id:04d}_{event_id:05d}_{tag}_{name}.yaml"
        else:
            filename = f"{event_id:05d}_{tag}_{name}.yaml"
        path = directory / filename

        payload = yaml.safe_dump(record, allow_unicode=False, sort_keys=False)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(payload)


def _sanitize_validation_name(value: str) -> str:
    """Normalize names for validation artifact filenames."""
    normalized = "".join(
        char if char.isalnum() or char in ("-", "_") else "_" for char in value
    )
    normalized = normalized.strip("_")
    return normalized or "event"


def _get_validation_caller() -> dict[str, Any]:
    """Capture caller metadata for validation events."""
    frame = inspect.currentframe()
    if frame is None:
        return {}
    caller = frame.f_back
    while caller is not None:
        module_name = caller.f_globals.get("__name__", "")
        if module_name != __name__:
            break
        caller = caller.f_back
    if caller is None:
        return {}
    return {
        "source_file": caller.f_code.co_filename,
        "source_line": caller.f_lineno,
        "source_function": caller.f_code.co_name,
        "source_module": caller.f_globals.get("__name__", ""),
    }


def _get_runtime_boot_id() -> int | None:
    """Return the runtime boot sequence number if available."""
    if not runtime_state.has_runtime_context():
        return None
    try:
        runtime = runtime_state.get_runtime_context()
    except Exception:
        return None
    return getattr(runtime, "boot_id", None)


def _emit_activity_record(record: dict[str, Any]) -> None:
    """Write a record to the activity log."""
    payload = {
        "timestamp": record["timestamp"],
        "level": record["level"],
        "tag": record["tag"],
        "message": record["message"],
    }
    if record.get("boot_id") is not None:
        payload["boot_id"] = record["boot_id"]
    if record.get("data") is not None:
        payload["data"] = record["data"]

    activity_logger = _ensure_activity_logger()
    activity_logger.info(json.dumps(payload, ensure_ascii=False))


def _emit_validation_record(
    tag: str, message: str, level: str, data: dict[str, Any]
) -> None:
    """Write a validation artifact record when validation is enabled."""
    if not _validation_enabled():
        return

    event_name = data.get("event") if isinstance(data, dict) else None
    boot_id = _get_runtime_boot_id()
    record = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "type": "validation_event",
        "tag": tag,
        "name": event_name or message,
        "level": level,
        "data": data,
    }
    if boot_id is not None:
        record["boot_id"] = boot_id
    caller = _get_validation_caller()
    if caller:
        record.update(caller)

    _write_validation_record(record)


def _emit_logfire_record(
    logfire_client: ModuleType,
    level: str,
    message: str,
    tag: str,
    data: dict[str, Any],
) -> None:
    """Mirror a record to Logfire if configured."""
    log_method = getattr(logfire_client, level, None)
    payload: dict[str, Any] = {"tag": tag}
    boot_id = _get_runtime_boot_id()
    if boot_id is not None:
        payload["boot_id"] = boot_id
    if data:
        payload["data"] = data

    if callable(log_method):
        log_method(message, **payload)
    else:
        logfire_client.info(message, level=level, **payload)


class UnifiedLogger:
    """Unified logger providing instrumentation and sink-based logging."""

    def __init__(
        self,
        tag: str,
        vault_context: str | None = None,
        default_sinks: Iterable[str] | None = None,
    ):
        """
        Initialize unified logger for a module or component.

        Args:
            tag: Module or component identifier
            vault_context: Optional explicit vault context (stored for convenience)
            default_sinks: Optional iterable of sink names for log output
        """
        self.tag = tag
        self.vault_context = vault_context
        self.default_sinks = (
            list(default_sinks)
            if default_sinks is not None
            else [
                "activity",
                "logfire",
            ]
        )
        self._logfire_instance: ModuleType | None = None  # Lazy initialization

    @property
    def _logfire(self) -> ModuleType:
        """Lazy-loaded Logfire instance."""
        if self._logfire_instance is None:
            self._logfire_instance = self._setup_logfire()
        return self._logfire_instance

    def _setup_logfire(self) -> ModuleType:
        """Set up Logfire client with console fallback."""
        refresh_logfire_configuration()
        global _logfire_instrumented
        if not _logfire_instrumented:
            # Basic instrumentation that doesn't require app instance
            logfire.instrument_pydantic()
            logfire.instrument_pydantic_ai()
            # SQLAlchemy instrumentation - commented out to reduce trace noise since
            # APScheduler logging already captures most SQLAlchemy activity.
            # Uncomment for detailed database query debugging when needed.
            # logfire.instrument_sqlalchemy()
            _logfire_instrumented = True
        return cast(ModuleType, logfire)

    # Sink-Based Logging

    def add_sink(self, *sinks: str) -> OneShotLogger:
        """Return a one-shot logger that appends sinks for the next log call."""
        return OneShotLogger(self, list(sinks), mode="append")

    def set_sinks(self, sinks: Iterable[str]) -> OneShotLogger:
        """Return a one-shot logger that replaces sinks for the next log call."""
        return OneShotLogger(self, list(sinks), mode="replace")

    def info(
        self, message: str, *, data: dict[str, Any] | None = None, **fields: Any
    ) -> None:
        """Info logging routed to configured sinks."""
        self._log("info", message, data=data, **fields)

    def warning(
        self, message: str, *, data: dict[str, Any] | None = None, **fields: Any
    ) -> None:
        """Warning logging routed to configured sinks."""
        self._log("warning", message, data=data, **fields)

    def error(
        self, message: str, *, data: dict[str, Any] | None = None, **fields: Any
    ) -> None:
        """Error logging routed to configured sinks."""
        self._log("error", message, data=data, **fields)

    def debug(
        self, message: str, *, data: dict[str, Any] | None = None, **fields: Any
    ) -> None:
        """Debug logging routed to configured sinks."""
        self._log("debug", message, data=data, **fields)

    @contextmanager
    def span(self, operation: str, **span_data: Any) -> Iterator[None]:
        """
        Manual instrumentation span for critical code paths.

        Usage:
            with logger.span("workflow_execution", vault=vault_name):
                # critical operation
                pass
        """
        with self._logfire.span(f"{self.tag}:{operation}", **span_data):
            yield

    @asynccontextmanager
    async def async_span(self, operation: str, **span_data: Any) -> AsyncIterator[None]:
        """
        Async manual instrumentation span for critical code paths.

        Usage:
            async with logger.async_span("api_request", endpoint="/status"):
                # async critical operation
                pass
        """
        async with self._logfire.span(f"{self.tag}:{operation}", **span_data):
            yield

    def _log(
        self,
        level: str,
        message: str,
        *,
        data: dict[str, Any] | None = None,
        sinks: Iterable[str] | None = None,
        sink_mode: str = "append",
        **fields: Any,
    ) -> None:
        payload: dict[str, Any] = {}
        if data:
            payload.update(data)
        if fields:
            payload.update(fields)

        resolved_sinks = list(self.default_sinks)
        if sinks:
            if sink_mode == "replace":
                resolved_sinks = list(sinks)
            else:
                for sink in sinks:
                    if sink not in resolved_sinks:
                        resolved_sinks.append(sink)

        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        boot_id = _get_runtime_boot_id()
        record: dict[str, Any] = {
            "timestamp": timestamp,
            "level": level,
            "tag": self.tag,
            "message": message,
            "data": payload or None,
        }
        if boot_id is not None:
            record["boot_id"] = boot_id

        if level == "warning" and _warnings_deduped():
            raw_issue = payload.get("issue")
            issue = str(raw_issue) if raw_issue is not None else None
            dedupe_key = (self.tag, message, issue)
            with _warning_dedupe_lock:
                global _warning_dedupe_boot_id
                global _warning_dedupe_keys
                if boot_id is not None and boot_id != _warning_dedupe_boot_id:
                    _warning_dedupe_keys.clear()
                    _warning_dedupe_boot_id = boot_id
                if dedupe_key in _warning_dedupe_keys:
                    return
                _warning_dedupe_keys.add(dedupe_key)

        if "activity" in resolved_sinks:
            _emit_activity_record(record)

        if "validation" in resolved_sinks:
            _emit_validation_record(self.tag, message, level, payload)

        if "logfire" in resolved_sinks:
            _emit_logfire_record(self._logfire, level, message, self.tag, payload)

    # Instrumentation Setup

    def setup_instrumentation(self, app: FastAPI | None = None) -> None:
        """
        Set up additional automatic instrumentation for the application.

        Note: Basic Logfire configuration and Pydantic/PydanticAI instrumentation
        are already set up in _setup_logfire(). This method adds app-specific
        instrumentation like FastAPI and Python logging.

        Args:
            app: Optional FastAPI app instance for request instrumentation
        """
        try:
            # Instrument FastAPI if app provided
            if app:
                logfire.instrument_fastapi(app)

            # Note: HTTP client instrumentation commented out - Pydantic-AI handles AI providers,
            # FastAPI handles internal calls, no need for raw HTTP tracing
            # self._logfire.instrument_requests()
            # self._logfire.instrument_httpx()

            # Capture Python logging for third-party libraries (like APScheduler)
            logging.basicConfig(
                handlers=[self._logfire.LogfireLoggingHandler()],
                level=logging.DEBUG,  # Set to DEBUG to capture detailed APScheduler information
            )

        except ImportError as e:
            # Expected failure when optional dependencies aren't available
            self.warning(f"Optional instrumentation dependency unavailable: {e}")
        except Exception as e:
            # Unexpected instrumentation failures should be visible
            self.error(f"Failed to set up instrumentation: {e}")
            raise


class OneShotLogger:
    """One-shot logger proxy to override sinks for a single call."""

    def __init__(self, base: UnifiedLogger, sinks: list[str], mode: str):
        self._base = base
        self._sinks = sinks
        self._mode = mode

    def info(
        self, message: str, *, data: dict[str, Any] | None = None, **fields: Any
    ) -> None:
        self._base._log(
            "info",
            message,
            data=data,
            sinks=self._sinks,
            sink_mode=self._mode,
            **fields,
        )

    def warning(
        self, message: str, *, data: dict[str, Any] | None = None, **fields: Any
    ) -> None:
        self._base._log(
            "warning",
            message,
            data=data,
            sinks=self._sinks,
            sink_mode=self._mode,
            **fields,
        )

    def error(
        self, message: str, *, data: dict[str, Any] | None = None, **fields: Any
    ) -> None:
        self._base._log(
            "error",
            message,
            data=data,
            sinks=self._sinks,
            sink_mode=self._mode,
            **fields,
        )

    def debug(
        self, message: str, *, data: dict[str, Any] | None = None, **fields: Any
    ) -> None:
        self._base._log(
            "debug",
            message,
            data=data,
            sinks=self._sinks,
            sink_mode=self._mode,
            **fields,
        )


# Settings bootstrap can emit activity events; all logger definitions must exist
# before eagerly reading configuration.
refresh_logfire_configuration(force=True)
