"""Harness-backed recovery state for one primary chat execution task."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai_harness.step_persistence import (
    InMemoryStepStore,
    RunRecord,
    StepEvent,
    StepPersistence,
    ToolEffectRecord,
    is_provider_valid,
)

from core.tools.base import ToolRecoveryPolicy, recovery_policy_from_tool_metadata

CHAT_RECOVERY_MAX_SNAPSHOTS_PER_RUN = 8


@dataclass(frozen=True)
class ChatRecoveryCheckpoint:
    """A settled provider-valid boundary selected for automatic continuation."""

    run_id: str
    step_index: int
    messages: list[ModelMessage]
    trimmed_failed_response: bool
    interrupted: bool = False


class ChatRecoveryStrategy(StrEnum):
    """Coordinator outcomes for a retryable failed agent run."""

    RESUME_SNAPSHOT = "resume_snapshot"
    REPLAY_NO_EFFECT = "replay_no_effect"
    TERMINAL_ROLLBACK_RESTART = "terminal_rollback_restart"
    MANUAL_REQUIRED = "manual_required"


@dataclass(frozen=True)
class ChatRecoveryDecision:
    """One recovery strategy selected from snapshot and tool-effect state."""

    strategy: ChatRecoveryStrategy
    reason: str
    checkpoint: ChatRecoveryCheckpoint | None = None
    completed_tool_count: int = 0
    unresolved_tool_count: int = 0


class ChatRunRecoveryCoordinator:
    """Own Harness recovery state for the lifetime of one logical chat task."""

    def __init__(
        self, *, tool_policies: dict[str, ToolRecoveryPolicy] | None = None
    ) -> None:
        self.store = InMemoryStepStore(
            max_snapshots_per_run=CHAT_RECOVERY_MAX_SNAPSHOTS_PER_RUN
        )
        self.tool_policies = dict(tool_policies or {})

    @classmethod
    def from_tools(cls, tools: list[Any]) -> ChatRunRecoveryCoordinator:
        """Build recovery policy lookup from resolved Pydantic tool metadata."""
        policies: dict[str, ToolRecoveryPolicy] = {}
        for tool in tools:
            name = str(getattr(tool, "name", "") or "").strip()
            if not name:
                continue
            policies[name] = recovery_policy_from_tool_metadata(
                getattr(tool, "metadata", None)
            )
        return cls(tool_policies=policies)

    def tool_policy(self, tool_name: str) -> ToolRecoveryPolicy:
        """Return the declared policy or the fail-closed default."""
        return self.tool_policies.get(tool_name, ToolRecoveryPolicy.UNKNOWN)

    def capability(self, *, session_id: str) -> StepPersistence:
        """Build the Harness capability that records this task's run attempts."""
        return StepPersistence(
            store=self.store,
            agent_name="primary-chat",
            metadata={"session_id": session_id},
        )

    async def select_settled_checkpoint(
        self, *, conversation_id: str
    ) -> ChatRecoveryCheckpoint | None:
        """Select a safe settled continuation point from the latest failed run."""
        decision = await self.decide(conversation_id=conversation_id)
        if decision.strategy is not ChatRecoveryStrategy.RESUME_SNAPSHOT:
            return None
        return decision.checkpoint

    async def decide(self, *, conversation_id: str) -> ChatRecoveryDecision:
        """Choose recovery from Harness state and developer-declared tool policy.

        Harness captures live history on failure. If a failed streaming model
        request appended a partial response, remove only that trailing response;
        the preceding settled tool-return request remains the continuation point.
        """
        runs = await self.store.list_runs(conversation_id=conversation_id)
        if not runs:
            return ChatRecoveryDecision(
                strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                reason="run_missing",
            )
        run = runs[-1]
        events = await self.store.list_events(run_id=run.run_id)
        if not events or events[-1].kind != "run_failed":
            return ChatRecoveryDecision(
                strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                reason="run_not_failed",
            )
        completed_tool_count = sum(
            event.kind == "tool_call_completed" for event in events
        )
        effects = await self._recorded_effects(run.run_id, events)
        if effects is None:
            return ChatRecoveryDecision(
                strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                reason="tool_effect_record_invalid",
                completed_tool_count=completed_tool_count,
            )
        failed = [effect for effect in effects if effect.status == "failed"]
        if any(
            self.tool_policy(effect.tool_name)
            in {ToolRecoveryPolicy.UNKNOWN, ToolRecoveryPolicy.MANUAL_REQUIRED}
            for effect in failed
        ):
            return ChatRecoveryDecision(
                strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                reason="failed_external_or_unknown_effect",
                completed_tool_count=completed_tool_count,
            )
        unresolved = await self.store.list_unresolved_tool_effects(run_id=run.run_id)
        if any(
            self.tool_policy(effect.tool_name)
            in {ToolRecoveryPolicy.UNKNOWN, ToolRecoveryPolicy.MANUAL_REQUIRED}
            for effect in unresolved
        ):
            return ChatRecoveryDecision(
                strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                reason="unresolved_external_or_unknown_effect",
                completed_tool_count=completed_tool_count,
                unresolved_tool_count=len(unresolved),
            )
        failed_vault = any(
            self.tool_policy(effect.tool_name) is ToolRecoveryPolicy.VAULT_TRANSACTIONAL
            for effect in failed
        )
        unresolved_vault = any(
            self.tool_policy(effect.tool_name) is ToolRecoveryPolicy.VAULT_TRANSACTIONAL
            for effect in unresolved
        )
        if failed_vault or unresolved_vault:
            # SDK failure settlement describes execution, not absence of side
            # effects. A whole-turn restart must roll back failed vault writes
            # and must not replay an external effect preserved by an earlier run.
            refusal = await self._whole_turn_restart_refusal(runs)
            return ChatRecoveryDecision(
                strategy=(
                    ChatRecoveryStrategy.MANUAL_REQUIRED
                    if refusal
                    else ChatRecoveryStrategy.TERMINAL_ROLLBACK_RESTART
                ),
                reason=refusal
                or (
                    "failed_vault_effect" if failed_vault else "unresolved_vault_effect"
                ),
                completed_tool_count=completed_tool_count,
                unresolved_tool_count=len(unresolved),
            )
        if unresolved:
            policies = {self.tool_policy(effect.tool_name) for effect in unresolved}
            if policies == {ToolRecoveryPolicy.REPLAY_SAFE}:
                snapshot = await self.store.latest_snapshot(
                    run_id=run.run_id,
                    include_interrupted=True,
                )
                if snapshot is None:
                    return ChatRecoveryDecision(
                        strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                        reason="snapshot_missing",
                        completed_tool_count=completed_tool_count,
                        unresolved_tool_count=len(unresolved),
                    )
                return ChatRecoveryDecision(
                    strategy=ChatRecoveryStrategy.REPLAY_NO_EFFECT,
                    reason="unresolved_replay_safe_effect",
                    checkpoint=ChatRecoveryCheckpoint(
                        run_id=run.run_id,
                        step_index=snapshot.step_index,
                        messages=list(snapshot.messages),
                        trimmed_failed_response=False,
                        interrupted=snapshot.state == "interrupted",
                    ),
                    completed_tool_count=completed_tool_count,
                    unresolved_tool_count=len(unresolved),
                )
            return ChatRecoveryDecision(
                strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                reason="unresolved_effect_policy_invalid",
                completed_tool_count=completed_tool_count,
                unresolved_tool_count=len(unresolved),
            )

        snapshot = await self.store.latest_snapshot(run_id=run.run_id)
        if snapshot is None:
            return ChatRecoveryDecision(
                strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                reason="snapshot_missing",
                completed_tool_count=completed_tool_count,
            )
        messages = list(snapshot.messages)
        trimmed_failed_response = bool(
            messages
            and isinstance(messages[-1], ModelResponse)
            and any(
                getattr(part, "part_kind", None) in {"text", "thinking"}
                for part in messages[-1].parts
            )
        )
        if trimmed_failed_response:
            messages.pop()
        if not messages or not is_provider_valid(messages):
            return ChatRecoveryDecision(
                strategy=ChatRecoveryStrategy.MANUAL_REQUIRED,
                reason="snapshot_invalid",
                completed_tool_count=completed_tool_count,
            )
        return ChatRecoveryDecision(
            strategy=ChatRecoveryStrategy.RESUME_SNAPSHOT,
            reason="settled_checkpoint",
            checkpoint=ChatRecoveryCheckpoint(
                run_id=run.run_id,
                step_index=snapshot.step_index,
                messages=messages,
                trimmed_failed_response=trimmed_failed_response,
            ),
            completed_tool_count=completed_tool_count,
        )

    async def _recorded_effects(
        self, run_id: str, events: list[StepEvent]
    ) -> list[ToolEffectRecord] | None:
        """Resolve observed calls through public ledger APIs, rejecting disagreement."""
        effects: dict[str, ToolEffectRecord] = {}
        for event in events:
            if event.kind not in {
                "tool_call_started",
                "tool_call_completed",
                "tool_call_failed",
            }:
                continue
            if not event.tool_call_id or not event.tool_name:
                return None
            effect = await self.store.get_tool_effect(
                run_id=run_id, tool_call_id=event.tool_call_id
            )
            if (
                effect is None
                or effect.run_id != run_id
                or effect.tool_call_id != event.tool_call_id
                or effect.tool_name != event.tool_name
                or effect.status not in {"started", "completed", "failed"}
            ):
                return None
            effects[event.tool_call_id] = effect
        return list(effects.values())

    async def _whole_turn_restart_refusal(self, runs: list[RunRecord]) -> str | None:
        """Audit every run retained by this task before replaying its whole turn."""
        for run in runs:
            events = await self.store.list_events(run_id=run.run_id)
            effects = await self._recorded_effects(run.run_id, events)
            if effects is None:
                return "tool_effect_record_invalid"
            # Unresolved records can precede their first event if execution
            # stopped between the ledger write and its event append.
            for unresolved in await self.store.list_unresolved_tool_effects(
                run_id=run.run_id
            ):
                recorded = await self.store.get_tool_effect(
                    run_id=run.run_id, tool_call_id=unresolved.tool_call_id
                )
                if recorded is None or recorded != unresolved:
                    return "tool_effect_record_invalid"
                effects.append(recorded)
            if any(
                self.tool_policy(effect.tool_name)
                not in {
                    ToolRecoveryPolicy.REPLAY_SAFE,
                    ToolRecoveryPolicy.VAULT_TRANSACTIONAL,
                }
                for effect in effects
            ):
                return "rollback_restart_external_or_unknown_effect"
        return None
