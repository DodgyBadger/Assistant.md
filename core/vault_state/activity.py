"""Attribution context for durable vault activities."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from core.runtime.execution_tasks import (
    ExecutionTaskSnapshot,
    get_current_execution_task,
    goal_context_from_metadata,
)


@dataclass(frozen=True)
class VaultActivityContext:
    """Provenance applied to vault mutations in the current context."""

    activity_id: str
    kind: str
    source: str
    scope: str | None
    label: str
    task_id: str | None = None
    goal_id: str | None = None
    step_id: str | None = None


_CURRENT_VAULT_ACTIVITY: ContextVar[VaultActivityContext | None] = ContextVar(
    "current_vault_activity",
    default=None,
)


def get_current_vault_activity() -> VaultActivityContext | None:
    """Return explicit activity attribution for the current context, if any."""
    return _CURRENT_VAULT_ACTIVITY.get()


def task_activity_id(task_id: str, vault_id: str) -> str:
    """Return the deterministic activity id for one task within one vault."""
    return f"task:{task_id}:{vault_id}"


def handle_task_terminal_for_activity(snapshot: ExecutionTaskSnapshot) -> None:
    """Project a process-local terminal task outcome into durable activities."""
    from core.vault_state.service import VaultStateService

    VaultStateService().finish_task_activities(
        task_id=snapshot.task_id,
        status=snapshot.status,
    )


@contextmanager
def use_vault_activity(context: VaultActivityContext) -> Iterator[VaultActivityContext]:
    """Apply explicit activity attribution for nested vault mutations."""
    token = _CURRENT_VAULT_ACTIVITY.set(context)
    try:
        yield context
    finally:
        _CURRENT_VAULT_ACTIVITY.reset(token)


@contextmanager
def recorded_vault_activity(*, label: str) -> Iterator[VaultActivityContext]:
    """Record a recoverable operation while preserving execution-task provenance."""
    from core.vault_state.service import VaultStateService

    task = get_current_execution_task()
    goal_id, step_id = goal_context_from_metadata(task.metadata if task else None)
    context = VaultActivityContext(
        activity_id=f"activity_{uuid.uuid4().hex}",
        kind=task.kind if task else "tool",
        source=task.source if task else "tool",
        scope=task.scope if task else None,
        label=task.label if task else label,
        task_id=task.task_id if task else None,
        goal_id=goal_id,
        step_id=step_id,
    )
    with use_vault_activity(context):
        try:
            yield context
        except Exception:
            VaultStateService().finish_activity(
                activity_id=context.activity_id, status="failed"
            )
            raise
        else:
            VaultStateService().finish_activity(
                activity_id=context.activity_id, status="completed"
            )
