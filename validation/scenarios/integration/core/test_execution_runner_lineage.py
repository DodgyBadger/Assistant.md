"""Real execution tasks and observable production lifecycle contracts."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application

pytestmark = pytest.mark.scenario


async def test_runner_child_authority_cancellation_and_detachment(
    app: Application,
) -> None:
    app.vault("ExecutionTaskRunnerVault")
    await app.start()
    from core.identity import SYSTEM_AUTHORITY, ExecutionAuthority
    from core.runtime.execution_tasks import ExecutionTaskKind, ExecutionTaskSource
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    parent = await runtime.task_coordinator.create_queued_task(
        kind=ExecutionTaskKind.CHAT,
        scope="runner:parent",
        source=ExecutionTaskSource.SYSTEM,
        label="runner-parent",
        authority=SYSTEM_AUTHORITY,
    )
    first_child = await runtime.task_coordinator.create_queued_task(
        kind=ExecutionTaskKind.CHAT,
        scope="runner:child",
        source=ExecutionTaskSource.SYSTEM,
        label="runner-first-child",
        authority=SYSTEM_AUTHORITY,
        parent_task_id=parent.task_id,
    )
    second_child = await runtime.task_coordinator.create_queued_task(
        kind=ExecutionTaskKind.CHAT,
        scope="runner:child",
        source=ExecutionTaskSource.SYSTEM,
        label="runner-second-child",
        authority=SYSTEM_AUTHORITY,
        parent_task_id=parent.task_id,
    )
    detached_log_checkpoint = app.events.checkpoint()
    completion_survivor = await runtime.task_coordinator.create_queued_task(
        kind=ExecutionTaskKind.DELEGATE,
        scope="runner:child",
        source=ExecutionTaskSource.TOOL,
        label="runner-completion-survivor",
        authority=SYSTEM_AUTHORITY,
        parent_task_id=parent.task_id,
        detached_from_parent_lifecycle=True,
    )
    app.events.require(
        "execution_task_created",
        after=detached_log_checkpoint,
        fields={
            "event": "execution_task_created",
            "status": "queued",
            "task_id": completion_survivor.task_id,
            "parent_task_id": parent.task_id,
            "detached_from_parent_lifecycle": True,
        },
    )
    try:
        await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.DELEGATE,
            scope="runner:missing-parent-child",
            source=ExecutionTaskSource.TOOL,
            label="runner-missing-parent-child",
            authority=SYSTEM_AUTHORITY,
            parent_task_id="task_missing_parent",
            detached_from_parent_lifecycle=True,
        )
    except RuntimeError as exc:
        assert "Parent execution task not found" in str(
            exc
        ), "Detached child lineage should require an existing parent"
    else:
        raise AssertionError("Detached child must reject missing lineage")
    try:
        await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.DELEGATE,
            scope="runner:cross-authority-child",
            source=ExecutionTaskSource.TOOL,
            label="runner-cross-authority-child",
            authority=ExecutionAuthority(principal_id="different-principal"),
            parent_task_id=parent.task_id,
            detached_from_parent_lifecycle=True,
        )
    except RuntimeError as exc:
        assert "authority must match" in str(
            exc
        ), "Detached child lineage should preserve parent authority"
    else:
        raise AssertionError("Detached child must reject cross-authority lineage")
    await runtime.task_coordinator.cancel_task(
        first_child.task_id, reason="validation_direct_child_cancel"
    )
    unaffected_parent = await runtime.task_coordinator.get_task(parent.task_id)
    unaffected_sibling = await runtime.task_coordinator.get_task(second_child.task_id)
    assert (
        unaffected_parent.status if unaffected_parent else None
    ) == "queued", "Direct child cancellation should not cancel its parent"
    assert (
        unaffected_sibling.status if unaffected_sibling else None
    ) == "queued", "Direct child cancellation should not cancel siblings"
    await runtime.task_coordinator.mark_completed(parent.task_id)
    cascaded_child = await runtime.task_coordinator.get_task(second_child.task_id)
    surviving_child = await runtime.task_coordinator.get_task(
        completion_survivor.task_id
    )
    assert (
        cascaded_child.status if cascaded_child else None
    ) == "cancelled", "A parent transition should cancel lifecycle-owned child tasks"
    assert (
        surviving_child.status if surviving_child else None
    ) == "queued", "Normal parent completion should preserve an opted-in child task"
    assert (
        surviving_child.detached_from_parent_lifecycle if surviving_child else None
    ) is True, "The child snapshot should expose its detached lifecycle policy"
    try:
        await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.DELEGATE,
            scope="runner:terminal-parent-child",
            source=ExecutionTaskSource.TOOL,
            label="runner-terminal-parent-child",
            authority=SYSTEM_AUTHORITY,
            parent_task_id=parent.task_id,
            detached_from_parent_lifecycle=True,
        )
    except RuntimeError as exc:
        assert "no longer accepting children" in str(
            exc
        ), "Detached child lineage should require an active parent at launch"
    else:
        raise AssertionError("Detached child must reject terminal lineage")
    await runtime.task_coordinator.cancel_task(
        completion_survivor.task_id, reason="validation_completion_survivor_cleanup"
    )
    parent_transitions = {
        "failed": runtime.task_coordinator.mark_failed,
        "cancelled": runtime.task_coordinator.mark_cancelled,
        "timed_out": runtime.task_coordinator.mark_timed_out,
        "skipped": runtime.task_coordinator.mark_skipped,
    }
    for transition_name, transition in parent_transitions.items():
        abnormal_parent = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.CHAT,
            scope=f"runner:{transition_name}-parent",
            source=ExecutionTaskSource.SYSTEM,
            label=f"runner-{transition_name}-parent",
            authority=SYSTEM_AUTHORITY,
        )
        detached_child = await runtime.task_coordinator.create_queued_task(
            kind=ExecutionTaskKind.DELEGATE,
            scope=f"runner:{transition_name}-parent-child",
            source=ExecutionTaskSource.TOOL,
            label=f"runner-{transition_name}-child",
            authority=SYSTEM_AUTHORITY,
            parent_task_id=abnormal_parent.task_id,
            detached_from_parent_lifecycle=True,
        )
        await transition(
            abnormal_parent.task_id, reason=f"validation_parent_{transition_name}"
        )
        surviving_abnormal_child = await runtime.task_coordinator.get_task(
            detached_child.task_id
        )
        assert (
            surviving_abnormal_child.status if surviving_abnormal_child else None
        ) == "queued", f"A detached child should survive parent {transition_name}"
        await runtime.task_coordinator.cancel_task(
            detached_child.task_id,
            reason=f"validation_{transition_name}_survivor_cleanup",
        )
