"""In-process execution task tracking for AssistantMD runtime work."""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from core.identity import ExecutionAuthority, use_execution_authority
from core.logger import UnifiedLogger


class ExecutionTaskStatus(StrEnum):
    """Lifecycle states for process-local execution tasks."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    SKIPPED = "skipped"


class ExecutionTaskKind(StrEnum):
    """Stable task kind values shared across runtime/API/tool callers."""

    CHAT = "chat"
    DELEGATE = "delegate"
    WORKFLOW = "workflow"
    HISTORY_COMPACTION = "history_compaction"
    INGESTION = "ingestion"


class ExecutionTaskSource(StrEnum):
    """Stable execution task source values."""

    API = "api"
    SCHEDULER = "scheduler"
    TOOL = "tool"
    SYSTEM = "system"


TERMINAL_STATUSES = {
    ExecutionTaskStatus.COMPLETED,
    ExecutionTaskStatus.FAILED,
    ExecutionTaskStatus.CANCELLED,
    ExecutionTaskStatus.TIMED_OUT,
    ExecutionTaskStatus.SKIPPED,
}


TERMINAL_STATUS_VALUES = {status.value for status in TERMINAL_STATUSES}

EXECUTION_TASK_RESULT_MAX_CHARS = 65_536


_CURRENT_EXECUTION_TASK: ContextVar[ExecutionTaskSnapshot | None] = ContextVar(
    "current_execution_task",
    default=None,
)


def chat_session_scope(session_id: str) -> str:
    """Return the stable task scope for a chat session."""
    return f"chat_session:{session_id}"


def workflow_vault_scope(vault_name: str) -> str:
    """Return the stable task scope for workflow work in one vault."""
    return f"workflow_vault:{vault_name}"


def ingestion_vault_scope(vault_name: str) -> str:
    """Return the stable task scope for ingestion work in one vault."""
    return f"ingestion_vault:{vault_name}"


def chat_task_label(session_id: str) -> str:
    """Return the stable label for chat execution tasks."""
    return f"chat:{session_id}"


def ingestion_task_label(job_id: int) -> str:
    """Return the stable label for one ingestion job task."""
    return f"ingestion:{job_id}"


def compaction_task_label(session_id: str) -> str:
    """Return the stable label for chat history compaction tasks."""
    return f"compact:{session_id}"


def get_current_execution_task() -> ExecutionTaskSnapshot | None:
    """Return the execution task associated with the current context, if any."""
    return _CURRENT_EXECUTION_TASK.get()


def goal_task_metadata(
    *,
    goal_id: str | None = None,
    step_id: str | None = None,
) -> dict[str, str]:
    """Return normalized goal context metadata for execution tasks."""
    metadata: dict[str, str] = {}
    clean_goal_id = _clean_goal_context_value(goal_id)
    clean_step_id = _clean_goal_context_value(step_id)
    if clean_goal_id:
        metadata["goal_id"] = clean_goal_id
    if clean_step_id:
        metadata["step_id"] = clean_step_id
    return metadata


def goal_context_from_metadata(
    metadata: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    """Extract optional goal context from execution task metadata."""
    if not isinstance(metadata, dict):
        return None, None
    return (
        _clean_goal_context_value(metadata.get("goal_id")),
        _clean_goal_context_value(metadata.get("step_id")),
    )


@dataclass(frozen=True)
class ExecutionTaskSnapshot:
    """Public immutable view of an execution task."""

    task_id: str
    kind: str
    scope: str
    source: str
    label: str
    principal_id: str
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    terminal_reason: str | None = None
    latest_event: str | None = None
    parent_task_id: str | None = None
    revision: int = 0
    last_heartbeat_at: datetime | None = None
    heartbeat_status: str | None = None
    last_progress_at: datetime | None = None
    health_status: str = "healthy"
    result: dict[str, Any] | None = None
    result_truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        """Return True when the task is in a terminal state."""
        return self.status in TERMINAL_STATUS_VALUES


@dataclass(frozen=True)
class ExecutionTaskCancellationResult:
    """Result of requesting cancellation for one task."""

    snapshot: ExecutionTaskSnapshot
    effective: bool


@dataclass(frozen=True)
class ExecutionTaskWaitResult:
    """Snapshots returned by one event-driven coordinator wait."""

    snapshots: tuple[ExecutionTaskSnapshot, ...]
    timed_out: bool


@dataclass
class _ExecutionTaskRecord:
    task_id: str
    kind: str
    scope: str
    source: str
    label: str
    authority: ExecutionAuthority
    status: ExecutionTaskStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested: bool = False
    terminal_reason: str | None = None
    latest_event: str | None = None
    parent_task_id: str | None = None
    revision: int = 0
    last_heartbeat_at: datetime | None = None
    heartbeat_status: str | None = None
    last_progress_at: datetime | None = None
    health_status: str = "healthy"
    result: dict[str, Any] | None = None
    result_truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    handle: asyncio.Task[Any] | None = None
    awaiting_handle: bool = False

    def snapshot(self) -> ExecutionTaskSnapshot:
        """Return a public snapshot without the private asyncio handle."""
        return ExecutionTaskSnapshot(
            task_id=self.task_id,
            kind=self.kind,
            scope=self.scope,
            source=self.source,
            label=self.label,
            principal_id=self.authority.principal_id,
            status=self.status.value,
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            cancel_requested=self.cancel_requested,
            terminal_reason=self.terminal_reason,
            latest_event=self.latest_event,
            parent_task_id=self.parent_task_id,
            revision=self.revision,
            last_heartbeat_at=self.last_heartbeat_at,
            heartbeat_status=self.heartbeat_status,
            last_progress_at=self.last_progress_at,
            health_status=self.health_status,
            result=dict(self.result) if self.result is not None else None,
            result_truncated=self.result_truncated,
            metadata=dict(self.metadata),
        )


class TaskCoordinator:
    """Track active and recently terminal execution tasks in this process."""

    def __init__(
        self,
        *,
        logger: UnifiedLogger | None = None,
        terminal_history_limit: int = 100,
        terminal_observers: list[Callable[[ExecutionTaskSnapshot], None]] | None = None,
    ) -> None:
        self._logger = logger or UnifiedLogger(tag="execution-tasks")
        self._terminal_history_limit = max(1, terminal_history_limit)
        self._terminal_observers = list(terminal_observers or [])
        self._records: dict[str, _ExecutionTaskRecord] = {}
        self._terminal_order: list[str] = []
        self._lock = asyncio.Lock()
        self._changed = asyncio.Condition(self._lock)

    @asynccontextmanager
    async def track_current_task(
        self,
        *,
        kind: str,
        scope: str,
        source: str,
        label: str,
        authority: ExecutionAuthority,
        metadata: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
        start_immediately: bool = True,
    ) -> AsyncIterator[ExecutionTaskSnapshot]:
        """Register the current asyncio task for the duration of one operation."""
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("TaskCoordinator requires an active asyncio task")

        task_id = self._new_task_id()
        await self._create_record(
            task_id=task_id,
            kind=kind,
            scope=scope,
            source=source,
            label=label,
            authority=authority,
            handle=current,
            metadata=metadata,
            parent_task_id=parent_task_id,
        )
        if start_immediately:
            await self.mark_started(task_id)

        snapshot = await self.get_task(task_id)
        if snapshot is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Execution task disappeared: {task_id}")
        token = _CURRENT_EXECUTION_TASK.set(snapshot)
        try:
            with use_execution_authority(authority):
                yield snapshot
        except asyncio.CancelledError:
            await self.mark_cancelled(task_id, reason="cancelled")
            raise
        except Exception as exc:
            await self.mark_failed(task_id, reason=f"{type(exc).__name__}: {exc}")
            raise
        else:
            await self.mark_completed(task_id)
        finally:
            _CURRENT_EXECUTION_TASK.reset(token)

    async def create_queued_task(
        self,
        *,
        kind: str,
        scope: str,
        source: str,
        label: str,
        authority: ExecutionAuthority,
        metadata: dict[str, Any] | None = None,
        parent_task_id: str | None = None,
        awaiting_handle: bool = False,
    ) -> ExecutionTaskSnapshot:
        """Create a queued task record before an asyncio handle exists."""
        task_id = self._new_task_id()
        await self._create_record(
            task_id=task_id,
            kind=kind,
            scope=scope,
            source=source,
            label=label,
            authority=authority,
            handle=None,
            metadata=metadata,
            parent_task_id=parent_task_id,
            awaiting_handle=awaiting_handle,
        )
        snapshot = await self.get_task(task_id)
        if snapshot is None:  # pragma: no cover - defensive
            raise RuntimeError(f"Execution task disappeared: {task_id}")
        return snapshot

    @asynccontextmanager
    async def track_existing_task(
        self,
        task_id: str,
    ) -> AsyncIterator[ExecutionTaskSnapshot]:
        """Attach the current asyncio task to an existing queued task record."""
        current = asyncio.current_task()
        if current is None:
            raise RuntimeError("TaskCoordinator requires an active asyncio task")

        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                raise RuntimeError(f"Execution task not found: {task_id}")
            if record.status in TERMINAL_STATUSES:
                raise RuntimeError(f"Execution task already terminal: {task_id}")
            if record.cancel_requested:
                snapshot = record.snapshot()
                should_cancel = True
            else:
                should_cancel = False
            record.handle = current
            record.awaiting_handle = False
            snapshot = record.snapshot()

        if should_cancel:
            raise asyncio.CancelledError

        token = _CURRENT_EXECUTION_TASK.set(snapshot)
        try:
            with use_execution_authority(record.authority):
                yield snapshot
        except asyncio.CancelledError:
            await self.mark_cancelled(task_id, reason="cancelled")
            raise
        except Exception as exc:
            await self.mark_failed(task_id, reason=f"{type(exc).__name__}: {exc}")
            raise
        else:
            await self.mark_completed(task_id)
        finally:
            _CURRENT_EXECUTION_TASK.reset(token)

    async def get_task(self, task_id: str) -> ExecutionTaskSnapshot | None:
        """Return one task snapshot by id."""
        async with self._lock:
            record = self._records.get(task_id)
            return record.snapshot() if record else None

    async def list_tasks(
        self,
        *,
        kind: str | None = None,
        scope: str | None = None,
        include_terminal: bool = True,
    ) -> list[ExecutionTaskSnapshot]:
        """Return task snapshots filtered by kind and scope."""
        async with self._lock:
            snapshots = []
            for record in self._records.values():
                if kind is not None and record.kind != kind:
                    continue
                if scope is not None and record.scope != scope:
                    continue
                if not include_terminal and record.status in TERMINAL_STATUSES:
                    continue
                snapshots.append(record.snapshot())

        return sorted(snapshots, key=lambda item: item.created_at)

    async def wait_for_tasks(
        self,
        task_ids: list[str],
        *,
        after_revisions: dict[str, int] | None = None,
        timeout_seconds: float | None = None,
        terminal_or_attention_only: bool = False,
        wake_on_attention: bool = True,
    ) -> ExecutionTaskWaitResult:
        """Wait for selected task state to become relevant without polling."""
        unique_ids = tuple(dict.fromkeys(task_ids))
        async with self._changed:
            if not unique_ids:
                return ExecutionTaskWaitResult(snapshots=(), timed_out=False)
            initial = self._snapshots_for_ids(unique_ids)
            revisions = (
                dict(after_revisions)
                if after_revisions is not None
                else {snapshot.task_id: snapshot.revision for snapshot in initial}
            )

            def _ready() -> bool:
                snapshots = self._snapshots_for_ids(unique_ids)
                if len(snapshots) != len(unique_ids):
                    return True
                if any(
                    snapshot.is_terminal
                    or (
                        wake_on_attention
                        and snapshot.health_status == "attention_required"
                    )
                    for snapshot in snapshots
                ):
                    return True
                if terminal_or_attention_only:
                    return False
                return any(
                    snapshot.revision > revisions.get(snapshot.task_id, -1)
                    for snapshot in snapshots
                )

            if _ready():
                return ExecutionTaskWaitResult(
                    snapshots=self._snapshots_for_ids(unique_ids),
                    timed_out=False,
                )

            try:
                if timeout_seconds is None:
                    await self._changed.wait_for(_ready)
                else:
                    async with asyncio.timeout(max(0.0, timeout_seconds)):
                        await self._changed.wait_for(_ready)
            except TimeoutError:
                return ExecutionTaskWaitResult(
                    snapshots=self._snapshots_for_ids(unique_ids),
                    timed_out=True,
                )

            return ExecutionTaskWaitResult(
                snapshots=self._snapshots_for_ids(unique_ids),
                timed_out=False,
            )

    async def cancel_task(
        self,
        task_id: str,
        *,
        reason: str = "cancel_requested",
    ) -> ExecutionTaskCancellationResult | None:
        """Request cancellation for one task by id."""
        handle: asyncio.Task[Any] | None = None
        mark_cancelled_without_handle = False
        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return None
            if record.status in TERMINAL_STATUSES:
                snapshot = record.snapshot()
                self._log_event(
                    "execution_task_cancel_ignored",
                    snapshot,
                    extra={"reason": reason, "ignored_reason": "task_terminal"},
                )
                return ExecutionTaskCancellationResult(
                    snapshot=snapshot, effective=False
                )
            record.cancel_requested = True
            record.latest_event = reason
            self._touch(record)
            handle = record.handle
            mark_cancelled_without_handle = (
                handle is None and not record.awaiting_handle
            )
            snapshot = record.snapshot()

        self._log_event("execution_task_cancel_requested", snapshot)
        if handle is not None and not handle.done():
            handle.cancel()
        elif mark_cancelled_without_handle:
            await self.mark_cancelled(task_id, reason=reason)
        return ExecutionTaskCancellationResult(snapshot=snapshot, effective=True)

    async def cancel_scope(
        self,
        scope: str,
        *,
        reason: str = "scope_cancel_requested",
    ) -> list[ExecutionTaskSnapshot]:
        """Request cancellation for all active tasks in one scope."""
        tasks = await self.list_tasks(scope=scope, include_terminal=False)
        snapshots = []
        for task in tasks:
            cancellation = await self.cancel_task(task.task_id, reason=reason)
            if cancellation is not None:
                snapshots.append(cancellation.snapshot)
        return snapshots

    async def update_metadata(self, task_id: str, metadata: dict[str, Any]) -> None:
        """Merge metadata into one task record."""
        snapshot = None
        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.metadata.update(metadata)
            self._touch(record)
            snapshot = record.snapshot()

        self._log_event("execution_task_metadata_updated", snapshot)

    async def heartbeat(
        self,
        task_id: str,
        *,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record observable progress for one non-terminal task."""
        snapshot = None
        async with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status in TERMINAL_STATUSES:
                return
            now = self._now()
            record.last_heartbeat_at = now
            record.heartbeat_status = status
            record.last_progress_at = now
            record.latest_event = "heartbeat"
            record.metadata["last_heartbeat_at"] = now.isoformat()
            record.metadata["heartbeat_status"] = status
            if metadata:
                record.metadata.update(metadata)
            self._touch(record)
            snapshot = record.snapshot()

        self._log_event("execution_task_heartbeat", snapshot)

    async def publish_progress(
        self,
        task_id: str,
        *,
        metadata: dict[str, Any],
        health_status: str = "healthy",
    ) -> None:
        """Publish bounded live progress without emitting per-event log noise."""
        async with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status in TERMINAL_STATUSES:
                return
            now = self._now()
            record.last_progress_at = now
            record.health_status = health_status
            record.metadata.update(metadata)
            record.latest_event = "progress"
            self._touch(record)

    async def record_result(self, task_id: str, result: dict[str, Any]) -> None:
        """Record one bounded, JSON-safe result before terminal publication."""
        snapshot = None
        async with self._lock:
            record = self._records.get(task_id)
            if record is None or record.status in TERMINAL_STATUSES:
                return
            bounded, truncated = _bound_execution_task_result(result)
            record.result = bounded
            record.result_truncated = truncated
            record.latest_event = "execution_task_result_recorded"
            self._touch(record)
            snapshot = record.snapshot()

        self._log_event(
            "execution_task_result_recorded",
            snapshot,
            extra={
                "result_chars": len(
                    json.dumps(snapshot.result, ensure_ascii=False, sort_keys=True)
                ),
                "truncated": snapshot.result_truncated,
                "artifact_reference_count": len(
                    (snapshot.result or {}).get("artifact_references", [])
                ),
            },
        )

    async def mark_completed(self, task_id: str, *, reason: str | None = None) -> None:
        """Mark one task completed."""
        await self._cancel_active_children(task_id, reason="parent_completed")
        await self._mark_terminal(
            task_id,
            ExecutionTaskStatus.COMPLETED,
            reason=reason,
            event="execution_task_completed",
        )

    async def mark_started(self, task_id: str) -> None:
        """Mark one queued task running."""
        await self._mark_started(task_id)

    async def mark_failed(self, task_id: str, *, reason: str | None = None) -> None:
        """Mark one task failed."""
        await self._cancel_active_children(task_id, reason="parent_failed")
        await self._mark_terminal(
            task_id,
            ExecutionTaskStatus.FAILED,
            reason=reason,
            event="execution_task_failed",
        )

    async def mark_cancelled(self, task_id: str, *, reason: str | None = None) -> None:
        """Mark one task cancelled."""
        await self._cancel_active_children(task_id, reason="parent_cancelled")
        await self._mark_terminal(
            task_id,
            ExecutionTaskStatus.CANCELLED,
            reason=reason,
            event="execution_task_cancelled",
        )

    async def mark_timed_out(self, task_id: str, *, reason: str | None = None) -> None:
        """Mark one task timed out."""
        await self._cancel_active_children(task_id, reason="parent_timed_out")
        await self._mark_terminal(
            task_id,
            ExecutionTaskStatus.TIMED_OUT,
            reason=reason,
            event="execution_task_timed_out",
        )

    async def mark_skipped(self, task_id: str, *, reason: str | None = None) -> None:
        """Mark one task skipped."""
        await self._cancel_active_children(task_id, reason="parent_skipped")
        await self._mark_terminal(
            task_id,
            ExecutionTaskStatus.SKIPPED,
            reason=reason,
            event="execution_task_skipped",
        )

    async def shutdown(self, *, reason: str = "runtime_shutdown") -> None:
        """Request cancellation for all active tasks."""
        active_tasks = await self.list_tasks(include_terminal=False)
        for task in active_tasks:
            await self.cancel_task(task.task_id, reason=reason)

    async def mark_unfinished_cancelled(
        self, *, reason: str = "runtime_shutdown"
    ) -> None:
        """Mark any remaining non-terminal task records cancelled."""
        active_tasks = await self.list_tasks(include_terminal=False)
        for task in active_tasks:
            await self.mark_cancelled(task.task_id, reason=reason)

    async def list_child_tasks(
        self,
        parent_task_id: str,
        *,
        include_terminal: bool = True,
    ) -> list[ExecutionTaskSnapshot]:
        """Return execution tasks directly owned by one parent task."""
        async with self._lock:
            snapshots = [
                record.snapshot()
                for record in self._records.values()
                if record.parent_task_id == parent_task_id
                and (include_terminal or record.status not in TERMINAL_STATUSES)
            ]
        return sorted(snapshots, key=lambda item: item.created_at)

    async def _cancel_active_children(self, task_id: str, *, reason: str) -> None:
        children = await self.list_child_tasks(task_id, include_terminal=False)
        for child in children:
            await self.cancel_task(child.task_id, reason=reason)

    async def _create_record(
        self,
        *,
        task_id: str,
        kind: str,
        scope: str,
        source: str,
        label: str,
        authority: ExecutionAuthority,
        handle: asyncio.Task[Any] | None,
        metadata: dict[str, Any] | None,
        parent_task_id: str | None,
        awaiting_handle: bool = False,
    ) -> None:
        now = self._now()
        record = _ExecutionTaskRecord(
            task_id=task_id,
            kind=str(kind),
            scope=scope,
            source=str(source),
            label=label,
            authority=authority,
            status=ExecutionTaskStatus.QUEUED,
            created_at=now,
            last_heartbeat_at=now,
            heartbeat_status="queued",
            last_progress_at=now,
            parent_task_id=parent_task_id,
            handle=handle,
            awaiting_handle=awaiting_handle,
            metadata=dict(metadata or {}),
        )
        record.metadata.setdefault("last_heartbeat_at", now.isoformat())
        record.metadata.setdefault("heartbeat_status", "queued")
        async with self._lock:
            self._records[task_id] = record
            self._changed.notify_all()

        self._log_event("execution_task_created", record.snapshot())

    async def _mark_started(self, task_id: str) -> None:
        snapshot = None
        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            record.status = ExecutionTaskStatus.RUNNING
            now = self._now()
            record.started_at = now
            record.latest_event = "started"
            record.last_heartbeat_at = now
            record.heartbeat_status = "started"
            record.last_progress_at = now
            record.metadata["last_heartbeat_at"] = now.isoformat()
            record.metadata["heartbeat_status"] = "started"
            self._touch(record)
            snapshot = record.snapshot()

        self._log_event("execution_task_started", snapshot)

    async def _mark_terminal(
        self,
        task_id: str,
        status: ExecutionTaskStatus,
        *,
        reason: str | None,
        event: str,
    ) -> None:
        snapshot = None
        async with self._lock:
            record = self._records.get(task_id)
            if record is None:
                return
            if record.status in TERMINAL_STATUSES:
                return
            record.status = status
            now = self._now()
            record.finished_at = now
            record.terminal_reason = reason
            record.latest_event = event
            record.last_heartbeat_at = now
            record.heartbeat_status = status.value
            record.last_progress_at = now
            record.metadata["last_heartbeat_at"] = now.isoformat()
            record.metadata["heartbeat_status"] = status.value
            record.handle = None
            self._touch(record)
            snapshot = record.snapshot()
            self._remember_terminal(task_id)

        self._log_event(event, snapshot)
        self._notify_terminal_observers(snapshot)

    def _remember_terminal(self, task_id: str) -> None:
        if task_id in self._terminal_order:
            self._terminal_order.remove(task_id)
        self._terminal_order.append(task_id)

        while len(self._terminal_order) > self._terminal_history_limit:
            stale_id = self._terminal_order.pop(0)
            stale_record = self._records.get(stale_id)
            if stale_record is None or stale_record.status not in TERMINAL_STATUSES:
                continue
            del self._records[stale_id]

    def _snapshots_for_ids(
        self, task_ids: tuple[str, ...]
    ) -> tuple[ExecutionTaskSnapshot, ...]:
        return tuple(
            record.snapshot()
            for task_id in task_ids
            if (record := self._records.get(task_id)) is not None
        )

    def _touch(self, record: _ExecutionTaskRecord) -> None:
        record.revision += 1
        self._changed.notify_all()

    def _log_event(
        self,
        event: str,
        snapshot: ExecutionTaskSnapshot,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        data = {
            "event": event,
            "task_id": snapshot.task_id,
            "kind": snapshot.kind,
            "scope": snapshot.scope,
            "source": snapshot.source,
            "label": snapshot.label,
            "principal_id": snapshot.principal_id,
            "status": snapshot.status,
            "cancel_requested": snapshot.cancel_requested,
            "terminal_reason": snapshot.terminal_reason,
            "last_heartbeat_at": (
                snapshot.last_heartbeat_at.isoformat()
                if snapshot.last_heartbeat_at
                else None
            ),
            "heartbeat_status": snapshot.heartbeat_status,
        }
        goal_id, step_id = goal_context_from_metadata(snapshot.metadata)
        if goal_id:
            data["goal_id"] = goal_id
        if step_id:
            data["step_id"] = step_id
        if extra:
            data.update(extra)
        log = (
            self._logger.set_sinks(["validation"])
            if event in {"execution_task_metadata_updated", "execution_task_heartbeat"}
            else self._logger.add_sink("validation")
        )
        log.info(
            event,
            data=data,
        )

    def _notify_terminal_observers(self, snapshot: ExecutionTaskSnapshot) -> None:
        """Notify process-local observers after a task reaches a terminal state."""
        for observer in self._terminal_observers:
            try:
                observer(snapshot)
            except Exception as exc:  # noqa: BLE001
                self._logger.add_sink("validation").error(
                    "execution_task_terminal_observer_failed",
                    data={
                        "event": "execution_task_terminal_observer_failed",
                        "task_id": snapshot.task_id,
                        "kind": snapshot.kind,
                        "status": snapshot.status,
                        "observer": getattr(
                            observer, "__name__", observer.__class__.__name__
                        ),
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )

    @staticmethod
    def _new_task_id() -> str:
        return f"task_{uuid.uuid4().hex}"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)


def _clean_goal_context_value(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bound_execution_task_result(
    result: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    normalized = _json_safe_value(result)
    if not isinstance(normalized, dict):  # pragma: no cover - input is typed as a dict
        normalized = {"value": normalized}
    serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
    if len(serialized) <= EXECUTION_TASK_RESULT_MAX_CHARS:
        return normalized, False

    bounded: dict[str, Any] = {"truncated": True}
    artifact_references = normalized.get("artifact_references")
    if isinstance(artifact_references, list):
        preserved_references: list[Any] = []
        for reference in artifact_references[:50]:
            candidate = [*preserved_references, reference]
            if len(_serialize_json(candidate)) > 4_096:
                break
            preserved_references = candidate
        if preserved_references:
            bounded["artifact_references"] = preserved_references

    preview_budget = max(
        0,
        EXECUTION_TASK_RESULT_MAX_CHARS - len(_serialize_json(bounded)) - 32,
    )
    bounded["preview"] = serialized[:preview_budget]
    while len(_serialize_json(bounded)) > EXECUTION_TASK_RESULT_MAX_CHARS:
        overflow = len(_serialize_json(bounded)) - EXECUTION_TASK_RESULT_MAX_CHARS
        bounded["preview"] = bounded["preview"][:-overflow]
    return bounded, True


def _json_safe_value(value: Any, *, _seen: set[int] | None = None) -> Any:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict | list | tuple | set):
        seen = _seen if _seen is not None else set()
        container_id = id(value)
        if container_id in seen:
            return "<recursive>"
        seen.add(container_id)
        try:
            if isinstance(value, dict):
                return {
                    str(key): _json_safe_value(item, _seen=seen)
                    for key, item in value.items()
                }
            return [_json_safe_value(item, _seen=seen) for item in value]
        finally:
            seen.remove(container_id)
    return str(value)


def _serialize_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
