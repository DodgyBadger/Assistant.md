"""API projections for process-local execution tasks."""

from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    chat_session_scope,
    workflow_vault_scope,
)
from core.runtime.state import get_runtime_context

from ..exceptions import APIException
from ..models import (
    ExecutionTaskCancelResponse,
    ExecutionTaskInfo,
    ExecutionTaskListResponse,
)


def _execution_task_info(snapshot: ExecutionTaskSnapshot) -> ExecutionTaskInfo:
    """Convert a runtime task snapshot into an API model."""
    return ExecutionTaskInfo(
        task_id=snapshot.task_id,
        kind=snapshot.kind,
        scope=snapshot.scope,
        source=snapshot.source,
        label=snapshot.label,
        status=snapshot.status,
        created_at=snapshot.created_at,
        started_at=snapshot.started_at,
        finished_at=snapshot.finished_at,
        cancel_requested=snapshot.cancel_requested,
        terminal_reason=snapshot.terminal_reason,
        latest_event=snapshot.latest_event,
        parent_task_id=snapshot.parent_task_id,
        detached_from_parent_lifecycle=snapshot.detached_from_parent_lifecycle,
        revision=snapshot.revision,
        last_heartbeat_at=snapshot.last_heartbeat_at,
        heartbeat_status=snapshot.heartbeat_status,
        last_progress_at=snapshot.last_progress_at,
        health_status=snapshot.health_status,
        metadata=dict(snapshot.metadata or {}),
    )


async def list_execution_tasks(
    *,
    kind: str | None = None,
    scope: str | None = None,
    include_terminal: bool = True,
) -> ExecutionTaskListResponse:
    """List process-local execution task snapshots."""
    runtime = get_runtime_context()
    snapshots = await runtime.execution_task_access.list_tasks(
        kind=kind,
        scope=scope,
        include_terminal=include_terminal,
    )
    return ExecutionTaskListResponse(
        tasks=[_execution_task_info(item) for item in snapshots]
    )


async def get_execution_task(task_id: str) -> ExecutionTaskInfo:
    """Return one process-local execution task snapshot."""
    runtime = get_runtime_context()
    snapshot = await runtime.execution_task_access.get_task(task_id)
    if snapshot is None:
        raise APIException(
            status_code=404,
            error_type="ExecutionTaskNotFound",
            message=f"Execution task not found: {task_id}",
            details={"task_id": task_id},
        )
    return _execution_task_info(snapshot)


async def cancel_execution_task(task_id: str) -> ExecutionTaskCancelResponse:
    """Request cancellation for one process-local execution task."""
    runtime = get_runtime_context()
    cancellation = await runtime.execution_task_access.cancel_task(task_id)
    if cancellation is None:
        raise APIException(
            status_code=404,
            error_type="ExecutionTaskNotFound",
            message=f"Execution task not found: {task_id}",
            details={"task_id": task_id},
        )
    task = _execution_task_info(cancellation.snapshot)
    return ExecutionTaskCancelResponse(
        task=task,
        cancelled=cancellation.effective,
    )


async def get_active_chat_task(session_id: str) -> ExecutionTaskInfo:
    """Return the running task, or the oldest queued task, for a chat session."""
    runtime = get_runtime_context()
    snapshots = await runtime.execution_task_access.list_tasks(
        scope=chat_session_scope(session_id),
        include_terminal=False,
    )
    if not snapshots:
        details: dict[str, str | int] = {"session_id": session_id}
        session = runtime.chat_session_access.get_session_by_id(session_id)
        if session is not None:
            details["history_revision"] = (
                runtime.chat_store.get_session_history_revision(
                    session_id,
                    session.vault_name,
                )
            )
        raise APIException(
            status_code=404,
            error_type="ExecutionTaskNotFound",
            message=f"No active execution task for chat session: {session_id}",
            details=details,
        )
    running = [snapshot for snapshot in snapshots if snapshot.status == "running"]
    selected = running[-1] if running else snapshots[0]
    return _execution_task_info(selected)


async def cancel_chat_session_task(session_id: str) -> ExecutionTaskCancelResponse:
    """Request cancellation for the active task in a chat session."""
    task = await get_active_chat_task(session_id)
    return await cancel_execution_task(task.task_id)


async def list_workflow_tasks(
    vault_name: str | None = None,
) -> ExecutionTaskListResponse:
    """List process-local workflow task snapshots."""
    scope = workflow_vault_scope(vault_name) if vault_name else None
    return await list_execution_tasks(
        kind=ExecutionTaskKind.WORKFLOW.value, scope=scope
    )
