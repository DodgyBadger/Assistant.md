"""Scoped public recovery-store fixtures; no claim of actual interruption generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

if TYPE_CHECKING:
    from core.chat.run_recovery import ChatRecoveryDecision
    from core.tools.base import ToolRecoveryPolicy


async def _unresolved_decision(
    policy: ToolRecoveryPolicy,
    *,
    additional_policy: ToolRecoveryPolicy | None = None,
) -> ChatRecoveryDecision:
    """Seed interrupted-frontier input, then observe the production decision.

    This tests consumption of stored recovery state, not crash generation.
    """
    from core.chat.run_recovery import ChatRunRecoveryCoordinator

    conversation_id = f"policy-{policy.value}-{additional_policy or 'single'}"
    run_id = f"run-{policy.value}"
    tool_name = "file_read"
    coordinator = ChatRunRecoveryCoordinator(tool_policies={tool_name: policy})
    await coordinator.store.register_run(
        RunRecord(run_id=run_id, conversation_id=conversation_id)
    )
    await coordinator.store.append_event(
        StepEvent(
            run_id=run_id,
            conversation_id=conversation_id,
            kind="run_started",
            step_index=0,
        )
    )
    await coordinator.store.append_event(
        StepEvent(
            run_id=run_id,
            conversation_id=conversation_id,
            kind="tool_call_started",
            step_index=1,
            tool_call_id="probe-1",
            tool_name=tool_name,
        )
    )
    await coordinator.store.save_snapshot(
        ContinuableSnapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            step_index=1,
            state="interrupted",
            messages=[
                ModelRequest(parts=[UserPromptPart(content="probe")]),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name=tool_name,
                            args={"operation": "read", "path": "notes/recovery.md"},
                            tool_call_id="probe-1",
                        )
                    ]
                ),
            ],
        )
    )
    await coordinator.store.record_tool_effect(
        ToolEffectRecord(
            run_id=run_id,
            tool_call_id="probe-1",
            tool_name=tool_name,
            status="started",
        )
    )
    if additional_policy is not None:
        coordinator.tool_policies["second_probe"] = additional_policy
        await coordinator.store.append_event(
            StepEvent(
                run_id=run_id,
                conversation_id=conversation_id,
                kind="tool_call_started",
                step_index=1,
                tool_call_id="probe-2",
                tool_name="second_probe",
            )
        )
        await coordinator.store.record_tool_effect(
            ToolEffectRecord(
                run_id=run_id,
                tool_call_id="probe-2",
                tool_name="second_probe",
                status="started",
            )
        )
    await coordinator.store.append_event(
        StepEvent(
            run_id=run_id,
            conversation_id=conversation_id,
            kind="run_failed",
            step_index=1,
        )
    )
    return await coordinator.decide(conversation_id=conversation_id)


async def _assert_snapshot_retention_is_bounded() -> None:
    """Pin the native Harness resource bound used by chat recovery."""
    from core.chat.run_recovery import (
        CHAT_RECOVERY_MAX_SNAPSHOTS_PER_RUN,
        ChatRunRecoveryCoordinator,
    )

    coordinator = ChatRunRecoveryCoordinator()
    run_id = "bounded-snapshots"
    conversation_id = "bounded-snapshots"
    await coordinator.store.register_run(
        RunRecord(run_id=run_id, conversation_id=conversation_id)
    )
    for step_index in range(CHAT_RECOVERY_MAX_SNAPSHOTS_PER_RUN + 1):
        await coordinator.store.save_snapshot(
            ContinuableSnapshot(
                run_id=run_id,
                conversation_id=conversation_id,
                step_index=step_index,
                state="complete",
                messages=[ModelRequest(parts=[UserPromptPart(content="probe")])],
            )
        )
    snapshots = await coordinator.store.list_snapshots(run_id=run_id)
    assert len(snapshots) == CHAT_RECOVERY_MAX_SNAPSHOTS_PER_RUN
    assert snapshots[0].step_index == 1
