"""Model-facing observation and control for process-local runtime jobs."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from enum import StrEnum
from time import monotonic
from typing import Annotated, Any

from pydantic import Field
from pydantic_ai.tools import Tool

from core.runtime.execution_tasks import ExecutionTaskSnapshot
from core.runtime.state import get_runtime_context

from .base import BaseTool, ToolRecoveryPolicy

JOB_WAIT_DEFAULT_SECONDS = 30.0
JOB_WAIT_MIN_SECONDS = 10.0
JOB_WAIT_MAX_SECONDS = 3_600.0
JOB_LIST_MAX_ITEMS = 50
JOB_MAX_IDS = 20

JobIds = Annotated[list[str] | None, Field(max_length=JOB_MAX_IDS)]
JobWaitSeconds = Annotated[
    float,
    Field(ge=JOB_WAIT_MIN_SECONDS, le=JOB_WAIT_MAX_SECONDS),
]


class JobOperation(StrEnum):
    """Operations supported by the model-facing job tool."""

    LIST = "list"
    STATUS = "status"
    WAIT = "wait"
    CANCEL = "cancel"


class Job(BaseTool):
    """Inspect and control process-local asynchronous jobs."""

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        """Return the generic job supervision tool."""

        async def job(
            *,
            operation: JobOperation,
            job_ids: JobIds = None,
            kind: str = "",
            include_terminal: bool = True,
            timeout_seconds: JobWaitSeconds = JOB_WAIT_DEFAULT_SECONDS,
        ) -> str:
            """Observe or control process-local asynchronous jobs.

            Start independent work before waiting. Wait only when the next step
            depends on a job result, and prefer one useful longer wait over polling.

            :param operation: Operation name: list, status, wait, or cancel.
            :param job_ids: Opaque job identifiers for status, wait, or cancel.
            :param kind: Optional job-kind filter for list.
            :param include_terminal: Whether list includes recently terminal jobs.
            :param timeout_seconds: Wait duration from 10 to 3600 seconds.
            """
            runtime = get_runtime_context()
            access = runtime.execution_task_access
            ids = _normalize_job_ids(job_ids)
            operation = JobOperation(operation)

            if operation == JobOperation.LIST:
                snapshots = await access.list_tasks(
                    kind=(kind or "").strip() or None,
                    include_terminal=include_terminal,
                )
                visible = snapshots[-JOB_LIST_MAX_ITEMS:]
                return _encode(
                    {
                        "operation": operation,
                        "jobs": [
                            _project_job_summary(snapshot) for snapshot in visible
                        ],
                        "total_jobs": len(snapshots),
                        "truncated": len(visible) != len(snapshots),
                    }
                )

            if operation in {JobOperation.STATUS, JobOperation.CANCEL} and not ids:
                raise ValueError(f"job {operation.value} requires at least one job_id")

            if operation == JobOperation.STATUS:
                return _encode(
                    {
                        "operation": operation,
                        "jobs": await _status_jobs(ids),
                    }
                )

            if operation == JobOperation.CANCEL:
                return _encode(
                    {
                        "operation": operation,
                        "jobs": await _cancel_jobs(ids),
                    }
                )

            _validate_wait_seconds(timeout_seconds)
            started = monotonic()
            if not ids:
                await asyncio.sleep(timeout_seconds)
                return _encode(
                    {
                        "operation": operation,
                        "jobs": [],
                        "timed_out": True,
                        "timer_completed": True,
                        "elapsed_seconds": monotonic() - started,
                    }
                )

            initial = await _status_jobs(ids)
            accessible_ids = [
                item["job_id"] for item in initial if item.get("outcome") != "not_found"
            ]
            if not accessible_ids:
                return _encode(
                    {
                        "operation": operation,
                        "jobs": initial,
                        "timed_out": False,
                        "timer_completed": False,
                    }
                )
            waited = await access.wait_for_tasks(
                accessible_ids,
                timeout_seconds=timeout_seconds,
            )
            snapshots_by_id = {item.task_id: item for item in waited.snapshots}
            jobs = [
                (
                    _project_job(snapshots_by_id[job_id], include_result=True)
                    if job_id in snapshots_by_id
                    else {"job_id": job_id, "outcome": "not_found"}
                )
                for job_id in ids
            ]
            return _encode(
                {
                    "operation": operation,
                    "jobs": jobs,
                    "timed_out": waited.timed_out,
                    "timer_completed": False,
                    "elapsed_seconds": monotonic() - started,
                }
            )

        return Tool(
            job,
            name="job",
            description=(
                "List, inspect, wait for, and cancel accessible process-local jobs. "
                "A wait may also be used as a timer when no job IDs are supplied."
            ),
        )

    @classmethod
    def get_recovery_policy(cls) -> ToolRecoveryPolicy:
        return ToolRecoveryPolicy.MANUAL_REQUIRED


async def _status_jobs(job_ids: list[str]) -> list[dict[str, Any]]:
    access = get_runtime_context().execution_task_access
    results: list[dict[str, Any]] = []
    for job_id in job_ids:
        snapshot = await access.get_task(job_id)
        results.append(
            _project_job(snapshot, include_result=True)
            if snapshot is not None
            else {"job_id": job_id, "outcome": "not_found"}
        )
    return results


async def _cancel_jobs(job_ids: list[str]) -> list[dict[str, Any]]:
    access = get_runtime_context().execution_task_access
    results: list[dict[str, Any]] = []
    for job_id in job_ids:
        cancellation = await access.cancel_task(job_id, reason="job_cancel_requested")
        if cancellation is None:
            results.append({"job_id": job_id, "outcome": "not_found"})
            continue
        current = await access.get_task(job_id)
        results.append(
            {
                **_project_job(
                    current or cancellation.snapshot,
                    include_result=True,
                ),
                "cancelled": cancellation.effective,
            }
        )
    return results


def _project_job(
    snapshot: ExecutionTaskSnapshot,
    *,
    include_result: bool,
) -> dict[str, Any]:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    projected: dict[str, Any] = {
        "job_id": snapshot.task_id,
        "kind": snapshot.kind,
        "label": snapshot.label,
        "status": snapshot.status,
        "parent_job_id": snapshot.parent_task_id,
        "created_at": _format_datetime(snapshot.created_at),
        "started_at": _format_datetime(snapshot.started_at),
        "finished_at": _format_datetime(snapshot.finished_at),
        "cancel_requested": snapshot.cancel_requested,
        "terminal_reason": snapshot.terminal_reason,
        "revision": snapshot.revision,
        "last_progress_at": _format_datetime(snapshot.last_progress_at),
        "health_status": snapshot.health_status,
        "queue_reason": metadata.get("queue_reason"),
        "queue_position": metadata.get("queue_position"),
        "active_tools": _bounded_list(metadata.get("active_tools"), limit=20),
        "recent_activity": _bounded_list(metadata.get("recent_activity"), limit=20),
        "tool_call_counts": _bounded_mapping(metadata.get("tool_call_counts")),
        "usage": _bounded_mapping(metadata.get("usage")),
    }
    if include_result and snapshot.result is not None:
        projected["result"] = snapshot.result
        projected["result_truncated"] = snapshot.result_truncated
    return projected


def _project_job_summary(snapshot: ExecutionTaskSnapshot) -> dict[str, Any]:
    metadata = snapshot.metadata if isinstance(snapshot.metadata, dict) else {}
    return {
        "job_id": snapshot.task_id,
        "kind": snapshot.kind,
        "label": snapshot.label,
        "status": snapshot.status,
        "parent_job_id": snapshot.parent_task_id,
        "created_at": _format_datetime(snapshot.created_at),
        "started_at": _format_datetime(snapshot.started_at),
        "finished_at": _format_datetime(snapshot.finished_at),
        "cancel_requested": snapshot.cancel_requested,
        "terminal_reason": snapshot.terminal_reason,
        "revision": snapshot.revision,
        "last_progress_at": _format_datetime(snapshot.last_progress_at),
        "health_status": snapshot.health_status,
        "queue_reason": metadata.get("queue_reason"),
        "queue_position": metadata.get("queue_position"),
    }


def _normalize_job_ids(job_ids: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for raw_job_id in job_ids or []:
        job_id = str(raw_job_id).strip()
        if job_id and job_id not in normalized:
            normalized.append(job_id)
    if len(normalized) > JOB_MAX_IDS:
        raise ValueError(f"job accepts at most {JOB_MAX_IDS} job_ids per call")
    return normalized


def _validate_wait_seconds(timeout_seconds: float) -> None:
    if timeout_seconds < JOB_WAIT_MIN_SECONDS:
        raise ValueError(
            f"job wait timeout_seconds must be at least {JOB_WAIT_MIN_SECONDS:g}"
        )
    if timeout_seconds > JOB_WAIT_MAX_SECONDS:
        raise ValueError(
            f"job wait timeout_seconds cannot exceed {JOB_WAIT_MAX_SECONDS:g}"
        )


def _bounded_list(value: Any, *, limit: int) -> list[Any]:
    return list(value[:limit]) if isinstance(value, list) else []


def _bounded_mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in list(value.items())[:20]}


def _format_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _encode(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
