"""Explicit, removable Compaction v1 to v2 session upgrades."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Literal, cast
from uuid import uuid4

from core.identity import ExecutionAuthority
from core.llm.thinking import ThinkingValue
from core.logger import UnifiedLogger
from core.memory.session_map.checkpoints import (
    commit_session_map_context_checkpoint,
)
from core.memory.session_map.evidence import (
    SessionMapEvidence,
    resolve_session_map_evidence_range,
)
from core.memory.session_map.models import SessionMapDraft
from core.memory.session_map.readiness import (
    SessionMapConfigurationReadiness,
    evaluate_session_map_configuration_readiness,
)
from core.memory.session_map.retained_evidence import (
    project_retained_session_map_evidence,
    project_retrieved_session_map_evidence,
)
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

from .chat_store import ChatStore, StoredChatMessage, StoredContextCheckpoint
from .compaction import (
    SteppedHistoryEvictionPlan,
    chat_session_history_lock,
    plan_session_map_reduction,
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

# Admission is short-lived; execution remains governed by the session gate and
# history lock. The task coordinator, not a separate pending-upgrade registry,
# owns active-operation state and releases it on cancellation or runtime restart.
_UPGRADE_ADMISSION_LOCK = asyncio.Lock()


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


@dataclass(frozen=True)
class _SessionContextStrategyUpgradeReadiness:
    """One non-mutating status/admission snapshot without duplicate hydration."""

    status: SessionContextStrategyStatus
    source_checkpoint: StoredContextCheckpoint | None = None
    configuration: SessionMapConfigurationReadiness | None = None


def get_session_context_strategy_status(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
) -> SessionContextStrategyStatus:
    """Resolve checkpoint pinning separately from the configured default."""
    return _evaluate_session_context_strategy_upgrade_readiness(
        store=store, session_id=session_id, vault_name=vault_name
    ).status


def _evaluate_session_context_strategy_upgrade_readiness(
    *, store: ChatStore, session_id: str, vault_name: str
) -> _SessionContextStrategyUpgradeReadiness:
    """Check cheap prerequisites before hydrating the canonical upgrade plan."""
    checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
    if checkpoint is None:
        return _SessionContextStrategyUpgradeReadiness(
            SessionContextStrategyStatus(
                strategy="unassigned",
                can_upgrade_to_v2=False,
                reason="session_not_pinned",
            )
        )
    if checkpoint.checkpoint_kind == "session_map":
        return _SessionContextStrategyUpgradeReadiness(
            SessionContextStrategyStatus(
                strategy="session_map",
                can_upgrade_to_v2=False,
                reason="already_compaction_v2",
            )
        )
    configuration = evaluate_session_map_configuration_readiness()
    configured = configuration.configured_strategy
    reason = (
        "compaction_v2_not_configured"
        if configured != "session_map"
        else configuration.reason
    )
    if reason == "ready":
        structure = store.get_raw_history_structure(session_id, vault_name)
        if structure.message_count == 0:
            reason = "canonical_history_empty"
        elif not structure.tool_history_ok:
            reason = "upgrade_plan_invalid_tool_history"
        elif structure.group_count == 1 and not structure.latest_group_complete:
            reason = "upgrade_plan_incomplete_latest_group"
    return _SessionContextStrategyUpgradeReadiness(
        status=SessionContextStrategyStatus(
            strategy="recovery_card",
            can_upgrade_to_v2=reason == "ready",
            reason=reason,
        ),
        source_checkpoint=checkpoint,
        configuration=configuration,
    )


async def start_session_context_strategy_upgrade(
    *,
    session_id: str,
    vault_name: str,
    authority: ExecutionAuthority,
) -> ExecutionTaskSnapshot:
    """Start or reuse one explicitly selected active upgrade."""
    operation_id = uuid4().hex
    try:
        async with _UPGRADE_ADMISSION_LOCK:
            return await _admit_session_context_strategy_upgrade(
                session_id=session_id,
                vault_name=vault_name,
                authority=authority,
            )
    except SessionContextStrategyUpgradeUnavailable as exc:
        logger.info(
            "context_strategy_upgrade_unavailable",
            data={
                "event": "context_strategy_upgrade_unavailable",
                "status": "unavailable",
                "operation_id": operation_id,
                "source": ExecutionTaskSource.API.value,
                "session_id": session_id,
                "vault_name": vault_name,
                "reason": exc.reason,
            },
        )
        raise


async def _admit_session_context_strategy_upgrade(
    *,
    session_id: str,
    vault_name: str,
    authority: ExecutionAuthority,
) -> ExecutionTaskSnapshot:
    """Atomically resolve eligibility and register one background operation."""
    runtime = get_runtime_context()
    session = runtime.chat_store.get_session(session_id, vault_name)
    if session is None:
        raise SessionContextStrategyUpgradeUnavailable("session_not_found")
    if session.owner_principal_id != authority.principal_id:
        raise SessionContextStrategyUpgradeUnavailable("session_owner_mismatch")
    active = await runtime.task_coordinator.list_tasks(
        kind=ExecutionTaskKind.CONTEXT_STRATEGY_UPGRADE,
        scope=chat_session_scope(session_id),
        include_terminal=False,
    )
    for task in active:
        if (
            task.principal_id == authority.principal_id
            and task.metadata.get("vault") == vault_name
        ):
            return task
    readiness = _evaluate_session_context_strategy_upgrade_readiness(
        store=runtime.chat_store,
        session_id=session_id,
        vault_name=vault_name,
    )
    if not readiness.status.can_upgrade_to_v2:
        raise SessionContextStrategyUpgradeUnavailable(readiness.status.reason)
    source_checkpoint = readiness.source_checkpoint
    if source_checkpoint is None:  # pragma: no cover - guarded by status
        raise SessionContextStrategyUpgradeUnavailable("recovery_checkpoint_missing")
    configuration = readiness.configuration
    if configuration is None:  # pragma: no cover - guarded by status
        raise SessionContextStrategyUpgradeUnavailable("compaction_v2_not_configured")
    author_model = configuration.author_model
    if author_model is None:  # pragma: no cover - checked by configuration
        raise SessionContextStrategyUpgradeUnavailable("author_model_not_configured")

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
                author_thinking=configuration.author_thinking,
                high_watermark=configuration.high_watermark_tokens,
                low_watermark=configuration.low_watermark_tokens,
                minimum_retained_groups=configuration.minimum_retained_groups,
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
            "status": "running",
            "task_id": task.task_id,
            "parent_task_id": task.parent_task_id,
            "source": task.source,
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
            retained = raw_messages[plan.eviction_end_index :]
            recent_evidence = project_retained_session_map_evidence(retained)
            retrieved_evidence = project_retrieved_session_map_evidence(
                store=store,
                session_id=session_id,
                vault_name=vault_name,
                retained=raw_messages,
            )
            logger.info(
                "context_strategy_upgrade_planned",
                data={
                    "event": "context_strategy_upgrade_planned",
                    "status": "planned",
                    "task_id": task.task_id,
                    "parent_task_id": task.parent_task_id,
                    "source": task.source,
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
                        retrieved_evidence=(
                            retrieved_evidence.messages
                            if pass_index == len(evidence)
                            else ()
                        ),
                        retrieved_evidence_truncated=(
                            retrieved_evidence.truncated
                            if pass_index == len(evidence)
                            else False
                        ),
                    ),
                    authority=authority,
                    source=ExecutionTaskSource.API,
                )
                # Writers outside this process need not share the history lock.
                # Stop between passes rather than spend the remaining inference
                # budget on a snapshot that can no longer be committed.
                if (
                    store.get_session_history_revision(session_id, vault_name)
                    != history_revision
                ):
                    raise SessionContextStrategyUpgradeUnavailable(
                        "session_history_changed"
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
                retrieved_evidence=retrieved_evidence.messages,
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
                retained_message_count=len(retained),
                raw_message_count=len(raw_messages),
            )
            logger.info(
                "context_strategy_upgrade_completed",
                data={
                    "event": "context_strategy_upgrade_completed",
                    "status": "completed",
                    "task_id": task.task_id,
                    "parent_task_id": task.parent_task_id,
                    "source": task.source,
                    **asdict(result),
                    "raw_messages_preserved": True,
                },
            )
            return result
    except asyncio.CancelledError:
        logger.info(
            "context_strategy_upgrade_cancelled",
            data={
                "event": "context_strategy_upgrade_cancelled",
                "status": "cancelled",
                "task_id": task.task_id,
                "parent_task_id": task.parent_task_id,
                "source": task.source,
                "session_id": session_id,
                "vault_name": vault_name,
                "source_checkpoint_id": source_checkpoint.checkpoint_id,
                "reason": "upgrade_cancelled",
            },
        )
        raise
    except Exception as exc:
        reason = _upgrade_failure_reason(exc)
        logger.warning(
            "context_strategy_upgrade_failed",
            data={
                "event": "context_strategy_upgrade_failed",
                "status": "failed",
                "issue": f"context_strategy_upgrade_failed:{task.task_id}",
                "task_id": task.task_id,
                "parent_task_id": task.parent_task_id,
                "source": task.source,
                "session_id": session_id,
                "vault_name": vault_name,
                "source_checkpoint_id": source_checkpoint.checkpoint_id,
                "error_type": type(exc).__name__,
                "reason": reason,
                "error": reason.replace("_", " "),
            },
        )
        raise


def _upgrade_failure_reason(error: Exception) -> str:
    """Keep closed lifecycle reasons without copying arbitrary exception text."""
    if isinstance(error, SessionContextStrategyUpgradeUnavailable) and error.reason in {
        "recovery_checkpoint_changed",
        "session_history_changed",
        "canonical_history_empty",
        "canonical_history_too_small",
    }:
        return error.reason
    return "upgrade_execution_failed"


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
    plan = plan_session_map_reduction(
        [message.message for message in raw_messages],
        high_watermark_tokens=high_watermark,
        low_watermark_tokens=low_watermark,
        minimum_retained_groups=minimum_retained_groups,
        history_revision=history_revision,
        force=True,
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
