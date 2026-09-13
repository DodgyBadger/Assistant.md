"""Validate tool-owned recovery metadata without duplicate capability summaries."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.authoring.shared.tool_binding import resolve_tool_binding
from core.chat.run_recovery import (
    CHAT_RECOVERY_MAX_SNAPSHOTS_PER_RUN,
    ChatRecoveryDecision,
    ChatRecoveryStrategy,
    ChatRunRecoveryCoordinator,
)
from core.tools.base import (
    BaseTool,
    ToolRecoveryPolicy,
    recovery_policy_from_tool_metadata,
    tool_recovery_metadata,
)
from core.tools.code_execution import CodeExecution
from validation.core.base_scenario import BaseScenario, with_local_user_authority


class ToolRecoveryPolicyScenario(BaseScenario):
    """Prove recovery policy travels with tools and unknown fails closed."""

    @with_local_user_authority
    async def test_scenario(self) -> None:
        vault = self.create_vault("ToolRecoveryPolicyVault")
        await self.start_system()
        try:
            binding = resolve_tool_binding(
                "file_read, file_write",
                vault_path=str(vault),
            )
            specs = {spec.name: spec for spec in binding.tool_specs}
            assert specs["file_read"].recovery_policy is ToolRecoveryPolicy.REPLAY_SAFE
            assert (
                specs["file_write"].recovery_policy
                is ToolRecoveryPolicy.VAULT_TRANSACTIONAL
            )

            coordinator = ChatRunRecoveryCoordinator.from_tools(binding.tool_functions)
            assert (
                coordinator.tool_policy("file_read") is ToolRecoveryPolicy.REPLAY_SAFE
            )
            assert (
                coordinator.tool_policy("file_write")
                is ToolRecoveryPolicy.VAULT_TRANSACTIONAL
            )
            assert coordinator.tool_policy("unregistered") is ToolRecoveryPolicy.UNKNOWN
            assert BaseTool.get_recovery_policy() is ToolRecoveryPolicy.UNKNOWN
            assert CodeExecution.get_recovery_policy() is ToolRecoveryPolicy.UNKNOWN
            assert tool_recovery_metadata(ToolRecoveryPolicy.REPLAY_SAFE) == {
                "recovery_policy": "replay_safe"
            }
            assert (
                recovery_policy_from_tool_metadata(
                    {"assistantmd": {"recovery_policy": "replay_safe"}}
                )
                is ToolRecoveryPolicy.REPLAY_SAFE
            )
            assert (
                recovery_policy_from_tool_metadata(
                    {"assistantmd": {"recovery_policy": "invalid"}}
                )
                is ToolRecoveryPolicy.UNKNOWN
            )
            await _assert_snapshot_retention_is_bounded()

            assert binding.tool_instructions == ""

            replay = await _unresolved_decision(ToolRecoveryPolicy.REPLAY_SAFE)
            assert replay.strategy is ChatRecoveryStrategy.REPLAY_NO_EFFECT
            assert replay.checkpoint is not None
            assert replay.checkpoint.interrupted is True
            await _assert_replay_executes_pending_tool(replay)

            rollback = await _unresolved_decision(
                ToolRecoveryPolicy.VAULT_TRANSACTIONAL
            )
            assert rollback.strategy is ChatRecoveryStrategy.TERMINAL_ROLLBACK_RESTART
            assert rollback.reason == "unresolved_vault_effect"

            manual = await _unresolved_decision(ToolRecoveryPolicy.MANUAL_REQUIRED)
            assert manual.strategy is ChatRecoveryStrategy.MANUAL_REQUIRED
            unknown = await _unresolved_decision(ToolRecoveryPolicy.UNKNOWN)
            assert unknown.strategy is ChatRecoveryStrategy.MANUAL_REQUIRED
            mixed = await _unresolved_decision(
                ToolRecoveryPolicy.VAULT_TRANSACTIONAL,
                additional_policy=ToolRecoveryPolicy.UNKNOWN,
            )
            assert mixed.strategy is ChatRecoveryStrategy.MANUAL_REQUIRED
            assert mixed.reason == "unresolved_external_or_unknown_effect"
            assert mixed.unresolved_tool_count == 2
        finally:
            await self.stop_system()
            self.teardown_scenario()


async def _unresolved_decision(
    policy: ToolRecoveryPolicy,
    *,
    additional_policy: ToolRecoveryPolicy | None = None,
) -> ChatRecoveryDecision:
    """Build one interrupted Harness frontier and return its policy decision."""
    conversation_id = f"policy-{policy.value}-{additional_policy or 'single'}"
    run_id = f"run-{policy.value}"
    tool_name = "probe"
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
                            args={},
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


async def _assert_replay_executes_pending_tool(
    decision: ChatRecoveryDecision,
) -> None:
    """Pin Pydantic's interrupted-history repair and pending-tool execution."""
    assert decision.checkpoint is not None
    effects: list[str] = []

    async def model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        assert any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in getattr(message, "parts", ())
        )
        return ModelResponse(parts=[TextPart(content="continued")])

    agent = Agent(FunctionModel(model))

    @agent.tool_plain
    async def probe() -> str:
        effects.append("replayed")
        return "read complete"

    result = await agent.run(None, message_history=decision.checkpoint.messages)
    assert result.output == "continued"
    assert effects == ["replayed"]
