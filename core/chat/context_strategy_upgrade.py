"""Explicit, removable Compaction v1 to v2 session upgrades."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, cast

from core.identity import ExecutionAuthority
from core.llm.thinking import ThinkingValue
from core.logger import UnifiedLogger
from core.memory.session_map.checkpoints import (
    commit_session_map_context_checkpoint,
)
from core.memory.session_map.evidence import (
    SessionMapEvidence,
    SessionMapMessageEvidence,
    resolve_session_map_evidence_range,
)
from core.memory.session_map.models import SessionMapDraft
from core.memory.session_map.service import (
    SessionMapAuthoringRequest,
    SessionMapAuthoringResult,
    run_session_map_authoring,
)
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    chat_session_scope,
    context_strategy_upgrade_task_label,
)
from core.runtime.state import get_runtime_context
from core.runtime.task_runner import ExecutionGatePolicy, ExecutionTaskSpec
from core.settings import (
    get_compaction_author_model,
    get_compaction_author_thinking,
    get_compaction_high_watermark_tokens,
    get_compaction_low_watermark_tokens,
    get_compaction_retained_turns,
    get_compaction_strategy,
)

from .chat_store import ChatStore, StoredChatMessage, StoredContextCheckpoint
from .compaction import (
    SteppedHistoryEvictionPlan,
    chat_session_history_lock,
    estimate_history_tokens,
    plan_stepped_history_eviction,
)

SessionContextStrategy = Literal[
    "unassigned",
    "recovery_card",
    "session_map",
]

logger = UnifiedLogger(
    tag="context-strategy-upgrade",
    default_sinks=["activity", "logfire", "validation"],
)


class SessionContextStrategyUpgradeUnavailable(ValueError):
    """Raised when one session cannot make the explicit V1-to-V2 transition."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason.replace("_", " "))


@dataclass(frozen=True)
class SessionContextStrategyStatus:
    """Backend-derived strategy and explicit upgrade eligibility."""

    strategy: SessionContextStrategy
    can_upgrade_to_v2: bool
    reason: str


@dataclass(frozen=True)
class SessionContextStrategyUpgradeResult:
    """Durable result of one explicit recovery-card migration."""

    session_id: str
    vault_name: str
    source_checkpoint_id: str
    checkpoint_id: str
    authoring_pass_count: int
    consumed_through_sequence_index: int
    retained_message_count: int
    raw_message_count: int


def get_session_context_strategy_status(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
) -> SessionContextStrategyStatus:
    """Resolve checkpoint pinning separately from the configured default."""
    checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
    if checkpoint is None:
        return SessionContextStrategyStatus(
            strategy="unassigned",
            can_upgrade_to_v2=False,
            reason="session_not_pinned",
        )
    if checkpoint.checkpoint_kind == "session_map":
        return SessionContextStrategyStatus(
            strategy="session_map",
            can_upgrade_to_v2=False,
            reason="already_compaction_v2",
        )
    configured = get_compaction_strategy()
    return SessionContextStrategyStatus(
        strategy="recovery_card",
        can_upgrade_to_v2=configured == "session_map",
        reason=(
            "ready" if configured == "session_map" else "compaction_v2_not_configured"
        ),
    )


async def start_session_context_strategy_upgrade(
    *,
    session_id: str,
    vault_name: str,
    authority: ExecutionAuthority,
) -> ExecutionTaskSnapshot:
    """Start one explicitly selected upgrade in the runtime background."""
    runtime = get_runtime_context()
    session = runtime.chat_store.get_session(session_id, vault_name)
    if session is None:
        raise SessionContextStrategyUpgradeUnavailable("session_not_found")
    if session.owner_principal_id != authority.principal_id:
        raise SessionContextStrategyUpgradeUnavailable("session_owner_mismatch")
    status = get_session_context_strategy_status(
        store=runtime.chat_store,
        session_id=session_id,
        vault_name=vault_name,
    )
    if not status.can_upgrade_to_v2:
        raise SessionContextStrategyUpgradeUnavailable(status.reason)
    source_checkpoint = runtime.chat_store.get_latest_context_checkpoint(
        session_id, vault_name
    )
    if source_checkpoint is None:  # pragma: no cover - guarded by status
        raise SessionContextStrategyUpgradeUnavailable("recovery_checkpoint_missing")
    author_model = get_compaction_author_model()
    if author_model is None:
        raise SessionContextStrategyUpgradeUnavailable("author_model_not_configured")
    author_thinking = get_compaction_author_thinking()
    high_watermark = get_compaction_high_watermark_tokens()
    low_watermark = get_compaction_low_watermark_tokens()
    if low_watermark >= high_watermark:
        raise SessionContextStrategyUpgradeUnavailable("invalid_watermarks")

    async def run(tracked_task: ExecutionTaskSnapshot) -> dict[str, object]:
        async def run_in_session_gate() -> dict[str, object]:
            await runtime.task_coordinator.mark_started(tracked_task.task_id)
            result = await _run_session_context_strategy_upgrade(
                task=tracked_task,
                store=runtime.chat_store,
                session_id=session_id,
                vault_name=vault_name,
                source_checkpoint=source_checkpoint,
                author_model=author_model,
                author_thinking=author_thinking,
                high_watermark=high_watermark,
                low_watermark=low_watermark,
                minimum_retained_groups=get_compaction_retained_turns(),
                authority=authority,
            )
            return asdict(result)

        return cast(
            dict[str, object],
            await runtime.task_runner.run_with_gate(
                tracked_task,
                ExecutionGatePolicy(
                    key=chat_session_scope(session_id),
                    queued_status="queued",
                    clear_metadata={
                        "queue_position": 0,
                        "waiting_for_task_id": None,
                    },
                ),
                run_in_session_gate,
            ),
        )

    return await runtime.task_runner.start_background(
        ExecutionTaskSpec(
            kind=ExecutionTaskKind.CONTEXT_STRATEGY_UPGRADE,
            scope=chat_session_scope(session_id),
            source=ExecutionTaskSource.API,
            label=context_strategy_upgrade_task_label(session_id),
            authority=authority,
            metadata={
                "vault": vault_name,
                "session_id": session_id,
                "source_checkpoint_id": source_checkpoint.checkpoint_id,
                "target_strategy": "session_map",
                "queued_by_session": True,
            },
        ),
        run,
        start_immediately=False,
    )


async def _run_session_context_strategy_upgrade(
    *,
    task: ExecutionTaskSnapshot,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    source_checkpoint: StoredContextCheckpoint,
    author_model: str,
    author_thinking: ThinkingValue,
    high_watermark: int,
    low_watermark: int,
    minimum_retained_groups: int,
    authority: ExecutionAuthority,
) -> SessionContextStrategyUpgradeResult:
    logger.info(
        "context_strategy_upgrade_started",
        data={
            "event": "context_strategy_upgrade_started",
            "task_id": task.task_id,
            "session_id": session_id,
            "vault_name": vault_name,
            "source_checkpoint_id": source_checkpoint.checkpoint_id,
            "history_revision": store.get_session_history_revision(
                session_id, vault_name
            ),
        },
    )
    try:
        async with chat_session_history_lock(
            session_id=session_id,
            vault_name=vault_name,
        ):
            latest = store.get_latest_context_checkpoint(session_id, vault_name)
            if (
                latest is None
                or latest.checkpoint_kind != "recovery_card"
                or latest.checkpoint_id != source_checkpoint.checkpoint_id
            ):
                raise SessionContextStrategyUpgradeUnavailable(
                    "recovery_checkpoint_changed"
                )
            history_revision = store.get_session_history_revision(
                session_id, vault_name
            )
            raw_messages = store.get_stored_messages(session_id, vault_name, mode="raw")
            plan = _plan_upgrade_eviction(
                raw_messages=raw_messages,
                history_revision=history_revision,
                high_watermark=high_watermark,
                low_watermark=low_watermark,
                minimum_retained_groups=minimum_retained_groups,
            )
            evidence = _build_upgrade_evidence(
                store=store,
                session_id=session_id,
                vault_name=vault_name,
                history_revision=history_revision,
                raw_messages=raw_messages,
                plan=plan,
            )
            recent_evidence = _build_recent_evidence(raw_messages, plan)
            logger.info(
                "context_strategy_upgrade_planned",
                data={
                    "event": "context_strategy_upgrade_planned",
                    "task_id": task.task_id,
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "target_boundary": plan.eviction_end_index - 1,
                    "estimated_raw_tokens": plan.estimated_tokens_before,
                    "retained_message_count": plan.retained_message_count,
                    "retained_group_count": (
                        plan.group_count - plan.evicted_group_count
                    ),
                    "authoring_pass_count": len(evidence),
                },
            )

            previous_map = SessionMapDraft()
            authored: SessionMapAuthoringResult | None = None
            for pass_index, item in enumerate(evidence, start=1):
                authored = await run_session_map_authoring(
                    SessionMapAuthoringRequest(
                        session_id=session_id,
                        vault_name=vault_name,
                        model_alias=author_model,
                        thinking=author_thinking,
                        previous_map=previous_map,
                        new_evidence=(item,),
                        recent_evidence=(
                            recent_evidence if pass_index == len(evidence) else ()
                        ),
                    ),
                    authority=authority,
                    source=ExecutionTaskSource.API,
                )
                previous_map = authored.draft
                await get_runtime_context().task_coordinator.heartbeat(
                    task.task_id,
                    status="authoring",
                    metadata={
                        "authoring_pass": pass_index,
                        "authoring_pass_count": len(evidence),
                    },
                )
            if authored is None:  # pragma: no cover - evidence is required
                raise RuntimeError("Context-strategy upgrade produced no authored map")
            if (
                store.get_session_history_revision(session_id, vault_name)
                != history_revision
            ):
                raise SessionContextStrategyUpgradeUnavailable(
                    "session_history_changed"
                )
            effective_before = (
                store.get_history(session_id, vault_name, mode="effective") or []
            )
            committed = commit_session_map_context_checkpoint(
                store=store,
                session_id=session_id,
                vault_name=vault_name,
                draft=authored.draft,
                previous_map=SessionMapDraft(),
                new_evidence=evidence,
                expected_history_revision=history_revision,
                message_count_before=len(effective_before),
                source="context_strategy_upgrade",
                authoring_task_id=authored.task_id,
                authoring_prompt_version=authored.prompt_contract_version,
                author_model_alias=authored.model_alias,
                author_thinking=authored.thinking,
                recent_evidence=recent_evidence,
                expected_previous_checkpoint_kind="recovery_card",
            )
            result = SessionContextStrategyUpgradeResult(
                session_id=session_id,
                vault_name=vault_name,
                source_checkpoint_id=source_checkpoint.checkpoint_id,
                checkpoint_id=committed.checkpoint.checkpoint_id,
                authoring_pass_count=len(evidence),
                consumed_through_sequence_index=(
                    committed.checkpoint.last_message_sequence_index
                ),
                retained_message_count=len(recent_evidence),
                raw_message_count=len(raw_messages),
            )
            logger.info(
                "context_strategy_upgrade_completed",
                data={
                    "event": "context_strategy_upgrade_completed",
                    "task_id": task.task_id,
                    **asdict(result),
                    "raw_messages_preserved": True,
                },
            )
            return result
    except Exception as exc:
        logger.warning(
            "context_strategy_upgrade_failed",
            data={
                "event": "context_strategy_upgrade_failed",
                "task_id": task.task_id,
                "session_id": session_id,
                "vault_name": vault_name,
                "source_checkpoint_id": source_checkpoint.checkpoint_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise


def _plan_upgrade_eviction(
    *,
    raw_messages: list[StoredChatMessage],
    history_revision: int,
    high_watermark: int,
    low_watermark: int,
    minimum_retained_groups: int,
) -> SteppedHistoryEvictionPlan:
    if not raw_messages:
        raise SessionContextStrategyUpgradeUnavailable("canonical_history_empty")
    model_messages = [message.message for message in raw_messages]
    estimated_tokens = estimate_history_tokens(model_messages)
    planned_high = high_watermark
    planned_low = low_watermark
    if estimated_tokens <= high_watermark:
        planned_high = max(1, estimated_tokens - 1)
        planned_low = min(low_watermark, max(0, planned_high - 1))
    if planned_low >= planned_high:
        raise SessionContextStrategyUpgradeUnavailable("canonical_history_too_small")
    plan = plan_stepped_history_eviction(
        model_messages,
        high_watermark_tokens=planned_high,
        low_watermark_tokens=planned_low,
        minimum_retained_groups=minimum_retained_groups,
        history_revision=history_revision,
    )
    if plan.status != "planned" or plan.eviction_end_index <= 0:
        raise SessionContextStrategyUpgradeUnavailable(f"upgrade_plan_{plan.reason}")
    return plan


def _build_upgrade_evidence(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    history_revision: int,
    raw_messages: list[StoredChatMessage],
    plan: SteppedHistoryEvictionPlan,
) -> tuple[SessionMapEvidence, ...]:
    target_sequence = raw_messages[plan.eviction_end_index - 1].sequence_index
    source_start = raw_messages[0].sequence_index
    historical_boundaries = [
        checkpoint.last_message_sequence_index
        for checkpoint in store.list_context_checkpoints(
            session_id,
            vault_name,
            checkpoint_kind="recovery_card",
        )
        if source_start <= checkpoint.last_message_sequence_index < target_sequence
    ]
    boundaries = sorted({*historical_boundaries, target_sequence})
    evidence: list[SessionMapEvidence] = []
    for boundary in boundaries:
        resolved = resolve_session_map_evidence_range(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            source_start_sequence_index=source_start,
            source_end_sequence_index=boundary,
            history_revision=history_revision,
        )
        if resolved.status != "resolved" or len(resolved.evidence) != 1:
            raise SessionContextStrategyUpgradeUnavailable(
                f"canonical_evidence_{resolved.reason}"
            )
        evidence.append(resolved.evidence[0])
        source_start = boundary + 1
    return tuple(evidence)


def _build_recent_evidence(
    raw_messages: list[StoredChatMessage],
    plan: SteppedHistoryEvictionPlan,
) -> tuple[SessionMapMessageEvidence, ...]:
    return tuple(
        SessionMapMessageEvidence(
            sequence_index=message.sequence_index,
            role=message.role,
            content_text=message.content_text,
        )
        for message in raw_messages[plan.eviction_end_index :]
    )
