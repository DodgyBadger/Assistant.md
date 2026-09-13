"""SQLite persistence for durable workflow execution history."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from core.database import connect_sqlite_from_system_db
from core.workflow_runs.schema import DB_NAME, ensure_workflow_run_schema

ACTIVE_WORKFLOW_RUN_STATUSES = {"queued", "running"}
TERMINAL_WORKFLOW_RUN_STATUSES = {
    "completed",
    "failed",
    "skipped",
    "cancelled",
    "timed_out",
    "missed",
}
WORKFLOW_RUN_RETENTION_DAYS = 90
WORKFLOW_RUN_MAX_PER_WORKFLOW = 500
MAX_TEXT_LENGTH = 4_000
MAX_FAILURE_JSON_LENGTH = 16_000
MAX_OUTPUT_FILES = 100


@dataclass(frozen=True)
class WorkflowRunRecord:
    """One durable workflow execution attempt."""

    run_id: str
    workflow_id: str
    workflow_name: str
    vault_name: str
    source: str
    owner_principal_id: str
    status: str
    queued_at: str
    task_id: str | None = None
    step_name: str | None = None
    scheduler_job_id: str | None = None
    scheduler_event_key: str | None = None
    scheduled_run_time: str | None = None
    reason: str | None = None
    message: str | None = None
    failure: dict[str, Any] | None = None
    output_files: tuple[str, ...] = field(default_factory=tuple)
    execution_time_seconds: float | None = None
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the stable API-ready workflow run representation."""
        payload = asdict(self)
        payload["output_files"] = list(self.output_files)
        return payload


class WorkflowRunStore:
    """Persistence boundary for workflow run lifecycle and history."""

    def __init__(self, system_root: str | None = None) -> None:
        self.system_root = system_root
        ensure_workflow_run_schema(system_root)

    def create_run(
        self,
        *,
        workflow_id: str,
        workflow_name: str,
        vault_name: str,
        source: str,
        owner_principal_id: str,
        task_id: str | None = None,
        step_name: str | None = None,
        scheduler_job_id: str | None = None,
        scheduled_run_time: str | None = None,
        queued_at: datetime | None = None,
    ) -> WorkflowRunRecord:
        """Create one queued workflow attempt before it waits for execution."""
        run_id = f"run_{uuid4().hex}"
        queued = _iso_timestamp(queued_at)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, workflow_id, workflow_name, vault_name, source,
                    owner_principal_id,
                    task_id, step_name, scheduler_job_id, scheduled_run_time,
                    status, queued_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?)
                """,
                (
                    run_id,
                    _required_text(workflow_id, "workflow_id"),
                    _required_text(workflow_name, "workflow_name"),
                    _required_text(vault_name, "vault_name"),
                    _required_text(source, "source"),
                    _required_text(owner_principal_id, "owner_principal_id"),
                    _optional_text(task_id),
                    _optional_text(step_name),
                    _optional_text(scheduler_job_id),
                    _optional_text(scheduled_run_time),
                    queued,
                ),
            )
            conn.commit()
            return self._require_run(conn, run_id)

    def mark_started(
        self,
        run_id: str,
        *,
        started_at: datetime | None = None,
    ) -> WorkflowRunRecord:
        """Mark a queued workflow attempt as running."""
        clean_run_id = _required_text(run_id, "run_id")
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workflow_runs
                SET status = 'running', started_at = COALESCE(started_at, ?)
                WHERE run_id = ? AND status = 'queued'
                """,
                (_iso_timestamp(started_at), clean_run_id),
            )
            conn.commit()
            return self._require_run(conn, clean_run_id)

    def finalize_run(
        self,
        run_id: str,
        *,
        status: str,
        reason: str | None = None,
        message: str | None = None,
        failure: dict[str, Any] | None = None,
        output_files: list[str] | tuple[str, ...] | None = None,
        execution_time_seconds: float | None = None,
        completed_at: datetime | None = None,
    ) -> WorkflowRunRecord:
        """Finalize an active run exactly once and apply bounded retention."""
        clean_run_id = _required_text(run_id, "run_id")
        clean_status = _terminal_status(status)
        clean_failure = _bounded_json(failure, MAX_FAILURE_JSON_LENGTH)
        clean_outputs = _output_files(output_files)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE workflow_runs
                SET status = ?, reason = ?, message = ?, failure_json = ?,
                    output_files_json = ?, execution_time_seconds = ?,
                    completed_at = ?
                WHERE run_id = ? AND status IN ('queued', 'running')
                """,
                (
                    clean_status,
                    _bounded_text(reason),
                    _bounded_text(message),
                    clean_failure,
                    json.dumps(list(clean_outputs), ensure_ascii=True),
                    _optional_duration(execution_time_seconds),
                    _iso_timestamp(completed_at),
                    clean_run_id,
                ),
            )
            if cursor.rowcount == 0:
                existing = self._require_run(conn, clean_run_id)
                if existing.status not in TERMINAL_WORKFLOW_RUN_STATUSES:
                    raise RuntimeError(
                        f"Workflow run could not be finalized: {clean_run_id}"
                    )
                return existing
            self._prune_in_conn(conn, now=completed_at)
            conn.commit()
            return self._require_run(conn, clean_run_id)

    def record_terminal_run(
        self,
        *,
        workflow_id: str,
        workflow_name: str,
        vault_name: str,
        source: str,
        owner_principal_id: str,
        status: str,
        reason: str | None = None,
        message: str | None = None,
        scheduler_job_id: str | None = None,
        scheduler_event_key: str | None = None,
        scheduled_run_time: str | None = None,
        completed_at: datetime | None = None,
    ) -> WorkflowRunRecord:
        """Record a terminal scheduler event that never entered the governor."""
        clean_event_key = _optional_text(scheduler_event_key)
        now = _iso_timestamp(completed_at)
        run_id = f"run_{uuid4().hex}"
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO workflow_runs (
                        run_id, workflow_id, workflow_name, vault_name, source,
                        owner_principal_id,
                        scheduler_job_id, scheduler_event_key, scheduled_run_time,
                        status, reason, message, queued_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        _required_text(workflow_id, "workflow_id"),
                        _required_text(workflow_name, "workflow_name"),
                        _required_text(vault_name, "vault_name"),
                        _required_text(source, "source"),
                        _required_text(owner_principal_id, "owner_principal_id"),
                        _optional_text(scheduler_job_id),
                        clean_event_key,
                        _optional_text(scheduled_run_time),
                        _terminal_status(status),
                        _bounded_text(reason),
                        _bounded_text(message),
                        _optional_text(scheduled_run_time) or now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                if clean_event_key is None:
                    raise
                row = conn.execute(
                    "SELECT * FROM workflow_runs WHERE scheduler_event_key = ?",
                    (clean_event_key,),
                ).fetchone()
                if row is None:
                    raise
                return _record_from_row(row)
            self._prune_in_conn(conn, now=completed_at)
            conn.commit()
            return self._require_run(conn, run_id)

    def get_run(self, run_id: str) -> WorkflowRunRecord | None:
        """Return a run by id."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?",
                (_required_text(run_id, "run_id"),),
            ).fetchone()
            return _record_from_row(row) if row is not None else None

    def get_latest_run(self, workflow_id: str) -> WorkflowRunRecord | None:
        """Return the latest terminal outcome for one workflow."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_latest_runs WHERE workflow_id = ?",
                (_required_text(workflow_id, "workflow_id"),),
            ).fetchone()
            return _record_from_row(row) if row is not None else None

    def list_runs(
        self, workflow_id: str, *, limit: int = 50
    ) -> list[WorkflowRunRecord]:
        """Return recent attempts for one workflow in reverse chronology."""
        clean_limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM workflow_runs
                WHERE workflow_id = ?
                ORDER BY COALESCE(completed_at, started_at, queued_at) DESC, run_id DESC
                LIMIT ?
                """,
                (_required_text(workflow_id, "workflow_id"), clean_limit),
            ).fetchall()
            return [_record_from_row(row) for row in rows]

    def list_runs_by_workflow_name(
        self,
        workflow_name: str,
        *,
        limit: int = 50,
    ) -> list[WorkflowRunRecord]:
        """Return recent attempts for one workflow name across vaults."""
        clean_limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM workflow_runs
                WHERE workflow_name = ?
                ORDER BY COALESCE(completed_at, started_at, queued_at) DESC, run_id DESC
                LIMIT ?
                """,
                (_required_text(workflow_name, "workflow_name"), clean_limit),
            ).fetchall()
            return [_record_from_row(row) for row in rows]

    def list_latest_runs(self) -> list[WorkflowRunRecord]:
        """Return the latest terminal outcome for every known workflow."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM workflow_latest_runs
                ORDER BY completed_at DESC, workflow_id
                """
            ).fetchall()
            return [_record_from_row(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = connect_sqlite_from_system_db(DB_NAME, self.system_root)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @staticmethod
    def _require_run(conn: sqlite3.Connection, run_id: str) -> WorkflowRunRecord:
        row = conn.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Workflow run not found: {run_id}")
        return _record_from_row(row)

    @staticmethod
    def _prune_in_conn(conn: sqlite3.Connection, *, now: datetime | None) -> None:
        cutoff = (now or datetime.now(UTC)) - timedelta(
            days=WORKFLOW_RUN_RETENTION_DAYS
        )
        conn.execute(
            """
            DELETE FROM workflow_runs
            WHERE completed_at IS NOT NULL
              AND completed_at < ?
              AND run_id NOT IN (SELECT run_id FROM workflow_latest_runs)
            """,
            (_iso_timestamp(cutoff),),
        )
        conn.execute(
            """
            DELETE FROM workflow_runs
            WHERE run_id IN (
                SELECT run_id
                FROM (
                    SELECT
                        run_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY workflow_id
                            ORDER BY completed_at DESC, run_id DESC
                        ) AS position
                    FROM workflow_runs
                    WHERE completed_at IS NOT NULL
                ) AS ranked
                WHERE position > ?
            )
            """,
            (WORKFLOW_RUN_MAX_PER_WORKFLOW,),
        )


def _record_from_row(row: sqlite3.Row) -> WorkflowRunRecord:
    failure = json.loads(row["failure_json"]) if row["failure_json"] else None
    outputs = tuple(
        str(value) for value in json.loads(row["output_files_json"] or "[]")
    )
    return WorkflowRunRecord(
        run_id=str(row["run_id"]),
        workflow_id=str(row["workflow_id"]),
        workflow_name=str(row["workflow_name"]),
        vault_name=str(row["vault_name"]),
        source=str(row["source"]),
        owner_principal_id=str(row["owner_principal_id"]),
        status=str(row["status"]),
        queued_at=str(row["queued_at"]),
        task_id=row["task_id"],
        step_name=row["step_name"],
        scheduler_job_id=row["scheduler_job_id"],
        scheduler_event_key=row["scheduler_event_key"],
        scheduled_run_time=row["scheduled_run_time"],
        reason=row["reason"],
        message=row["message"],
        failure=failure,
        output_files=outputs,
        execution_time_seconds=row["execution_time_seconds"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
    )


def _required_text(value: Any, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field_name} is required")
    return clean


def _optional_text(value: Any) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _bounded_text(value: Any) -> str | None:
    clean = _optional_text(value)
    return clean[:MAX_TEXT_LENGTH] if clean else None


def _bounded_json(value: dict[str, Any] | None, max_length: int) -> str | None:
    if not value:
        return None
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True)
    if len(encoded) > max_length:
        return json.dumps(
            {"truncated": True, "summary": encoded[: max_length - 40]},
            ensure_ascii=True,
            sort_keys=True,
        )
    return encoded


def _output_files(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return tuple(
        str(value)[:MAX_TEXT_LENGTH] for value in (values or ())[:MAX_OUTPUT_FILES]
    )


def _terminal_status(value: str) -> str:
    clean = _required_text(value, "status").lower()
    if clean not in TERMINAL_WORKFLOW_RUN_STATUSES:
        allowed = ", ".join(sorted(TERMINAL_WORKFLOW_RUN_STATUSES))
        raise ValueError(
            f"Unsupported workflow run terminal status '{clean}'. Expected: {allowed}"
        )
    return clean


def _optional_duration(value: float | None) -> float | None:
    if value is None:
        return None
    duration = float(value)
    return max(duration, 0.0)


def _iso_timestamp(value: datetime | None = None) -> str:
    timestamp = value or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    return timestamp.astimezone(UTC).isoformat(timespec="milliseconds")
