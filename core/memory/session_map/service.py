"""Opt-in shadow maintenance for cumulative live session maps."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue

from core.chat.chat_store import ChatStore
from core.identity import ExecutionAuthority
from core.llm.decision import (
    DecisionClassifier,
    DecisionResult,
    build_decision_classifier,
)
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    ExecutionTaskStatus,
)
from core.runtime.task_runner import (
    ExecutionConcurrencyPolicy,
    ExecutionGatePolicy,
    ExecutionTaskHooks,
    ExecutionTaskRunner,
    ExecutionTaskRunOutcome,
    ExecutionTaskSpec,
)
from core.settings import (
    get_live_session_memory_author_model,
    get_live_session_memory_broad_change_threshold,
    get_live_session_memory_decision_model,
    get_live_session_memory_eligibility_turns,
    get_live_session_memory_field_change_threshold,
    get_live_session_memory_max_concurrent_tasks,
    get_live_session_memory_max_pending_tokens,
    get_live_session_memory_max_pending_turns,
    get_live_session_memory_mode,
    get_live_session_memory_task_timeout_seconds,
)

from .authoring import (
    SESSION_MAP_AUTHORING_PROMPT_VERSION,
    CanonicalMapMessage,
    SessionMapAuthoringRequest,
    SessionMapAuthoringResult,
    author_session_map_patch,
    project_canonical_map_message,
)
from .change_detection import (
    SESSION_CUMULATIVE_CHANGE_PROMPT_CONTRACT_VERSION,
    SESSION_MAP_CUMULATIVE_CHANGE_FIELDS,
    SessionMapCumulativeChangeSignals,
    build_cumulative_session_map_change_request,
)
from .models import SessionMap, render_session_map
from .store import SessionMapMaintenanceState, SessionMapStore

logger = UnifiedLogger(tag="session-map-service")

_SESSION_MEMORY_CONCURRENCY_KEY = "session_memory_models"
_SESSION_MAP_RENDER_BUDGET = 20_000


class SessionMapAuthor(Protocol):
    """Injectable boundary for one generative map-author call."""

    def __call__(
        self,
        *,
        model_alias: str,
        request: SessionMapAuthoringRequest,
        created_at: datetime,
    ) -> Awaitable[SessionMapAuthoringResult]: ...


@dataclass(frozen=True)
class SessionMemoryPolicy:
    """One immutable settings snapshot for a maintenance decision."""

    mode: str
    decision_model: str
    author_model: str
    eligibility_turns: int
    broad_change_threshold: float
    field_change_threshold: float
    max_pending_turns: int
    max_pending_tokens: int
    task_timeout_seconds: float
    max_concurrent_tasks: int

    @classmethod
    def from_settings(cls) -> SessionMemoryPolicy:
        """Load the current tuning policy from general settings."""
        return cls(
            mode=get_live_session_memory_mode(),
            decision_model=get_live_session_memory_decision_model(),
            author_model=get_live_session_memory_author_model(),
            eligibility_turns=get_live_session_memory_eligibility_turns(),
            broad_change_threshold=(get_live_session_memory_broad_change_threshold()),
            field_change_threshold=get_live_session_memory_field_change_threshold(),
            max_pending_turns=get_live_session_memory_max_pending_turns(),
            max_pending_tokens=get_live_session_memory_max_pending_tokens(),
            task_timeout_seconds=get_live_session_memory_task_timeout_seconds(),
            max_concurrent_tasks=get_live_session_memory_max_concurrent_tasks(),
        )

    @property
    def enabled(self) -> bool:
        return self.mode == "observe"


class SessionMapService:
    """Coordinate durable post-turn accounting and tracked shadow maintenance."""

    def __init__(
        self,
        *,
        chat_store: ChatStore,
        map_store: SessionMapStore,
        task_runner: ExecutionTaskRunner,
        policy_loader: Callable[[], SessionMemoryPolicy] = (
            SessionMemoryPolicy.from_settings
        ),
        classifier_builder: Callable[[str], DecisionClassifier] = (
            build_decision_classifier
        ),
        author: SessionMapAuthor = author_session_map_patch,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._chat_store = chat_store
        self._map_store = map_store
        self._task_runner = task_runner
        self._policy_loader = policy_loader
        self._classifier_builder = classifier_builder
        self._author = author
        self._now = now or (lambda: datetime.now(UTC))
        self._schedule_lock = asyncio.Lock()
        self._scheduled_sessions: set[str] = set()

    def record_completed_turn(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        vault_name: str,
    ) -> int | None:
        """Durably account for a completed turn when shadow mode is enabled."""
        if not self._policy_loader().enabled:
            return None
        return self._map_store.record_completed_turn(
            connection,
            session_id=session_id,
            vault_name=vault_name,
        )

    async def maybe_schedule_after_turn(
        self,
        *,
        session_id: str,
        vault_name: str,
        authority: ExecutionAuthority,
        parent_task_id: str,
    ) -> ExecutionTaskSnapshot | None:
        """Schedule one coalesced maintenance task when current policy is eligible."""
        policy = self._policy_loader()
        if not policy.enabled:
            return None
        state = self._map_store.get_maintenance_state(session_id, vault_name)
        if state is None or not _eligible(state, policy):
            return None
        async with self._schedule_lock:
            if session_id in self._scheduled_sessions:
                return None
            self._scheduled_sessions.add(session_id)

        async def _run(task: ExecutionTaskSnapshot) -> ExecutionTaskRunOutcome:
            outcome: ExecutionTaskRunOutcome | None = None
            try:
                raw_outcome = await self._task_runner.run_with_concurrency(
                    task,
                    ExecutionConcurrencyPolicy(
                        key=_SESSION_MEMORY_CONCURRENCY_KEY,
                        limit=policy.max_concurrent_tasks,
                        queued_status="queued_for_session_memory_capacity",
                        clear_metadata={
                            "queue_position": 0,
                            "active_task_ids": [],
                        },
                    ),
                    lambda: self._task_runner.run_with_gate(
                        task,
                        ExecutionGatePolicy(
                            key=f"session_memory:{session_id}",
                            queued_status="queued_for_session_memory",
                            clear_metadata={
                                "queue_position": 0,
                                "waiting_for_task_id": None,
                            },
                        ),
                        lambda: self._run_attempt(
                            task=task,
                            session_id=session_id,
                            vault_name=vault_name,
                            policy=policy,
                        ),
                    ),
                )
                if not isinstance(raw_outcome, ExecutionTaskRunOutcome):
                    raise RuntimeError(
                        "session-memory task returned an unexpected outcome"
                    )
                outcome = raw_outcome
                return outcome
            finally:
                async with self._schedule_lock:
                    self._scheduled_sessions.discard(session_id)
                if outcome is not None and outcome.status in {
                    ExecutionTaskStatus.COMPLETED,
                    ExecutionTaskStatus.SKIPPED,
                }:
                    try:
                        await self.maybe_schedule_after_turn(
                            session_id=session_id,
                            vault_name=vault_name,
                            authority=authority,
                            parent_task_id=task.task_id,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "session_map_catch_up_dispatch_failed",
                            data={
                                "event": "session_map_catch_up_dispatch_failed",
                                "status": "failed",
                                "session_id": session_id,
                                "vault_name": vault_name,
                                "error_type": type(exc).__name__,
                                "issue": "session_map_catch_up_dispatch",
                            },
                        )

        hooks = ExecutionTaskHooks(
            on_cancelled=lambda _task_id: self._release_frozen_attempt(
                session_id,
                vault_name,
                error_type="cancelled",
                retryable=True,
            ),
            on_failed=lambda _task_id, exc: self._release_frozen_attempt(
                session_id,
                vault_name,
                error_type=type(exc).__name__,
                retryable=True,
            ),
            on_timed_out=lambda _task_id, _timeout, _reason: (
                self._release_frozen_attempt(
                    session_id,
                    vault_name,
                    error_type="timeout",
                    retryable=True,
                )
            ),
        )
        try:
            return await self._task_runner.start_background(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.SESSION_MEMORY,
                    scope=f"session_memory:{session_id}",
                    source=ExecutionTaskSource.SYSTEM,
                    label=f"session-memory:{session_id}",
                    authority=authority,
                    metadata={
                        "vault": vault_name,
                        "session_id": session_id,
                        "mode": policy.mode,
                        "decision_model": policy.decision_model,
                        "author_model": policy.author_model,
                    },
                    parent_task_id=parent_task_id,
                    detached_from_parent_lifecycle=True,
                    timeout_seconds=policy.task_timeout_seconds,
                    timeout_reason="session_memory_timeout",
                ),
                _run,
                hooks=hooks,
                start_immediately=False,
            )
        except BaseException:
            async with self._schedule_lock:
                self._scheduled_sessions.discard(session_id)
            raise

    async def _run_attempt(
        self,
        *,
        task: ExecutionTaskSnapshot,
        session_id: str,
        vault_name: str,
        policy: SessionMemoryPolicy,
    ) -> ExecutionTaskRunOutcome:
        if not self._policy_loader().enabled:
            return ExecutionTaskRunOutcome(
                value={"status": "disabled"},
                status=ExecutionTaskStatus.SKIPPED,
                reason="session_memory_disabled",
            )
        state = self._map_store.get_maintenance_state(session_id, vault_name)
        if state is None or not _eligible(state, policy):
            return ExecutionTaskRunOutcome(
                value={"status": "not_eligible"},
                status=ExecutionTaskStatus.SKIPPED,
                reason="session_memory_not_eligible",
            )
        frozen: SessionMapMaintenanceState | None = None
        decision_data: dict[str, JsonValue] | None = None
        authoring_data: dict[str, JsonValue] | None = None
        try:
            frozen = self._map_store.freeze_attempt(
                session_id,
                vault_name,
                task_id=task.task_id,
            )
            current_map = self._map_store.get_latest_revision(session_id, vault_name)
            if current_map is None:
                current_map = SessionMap.empty(
                    session_id=session_id,
                    created_at=self._now(),
                )
            delta = self._load_delta(session_id, vault_name, frozen)
            classifier = self._classifier_builder(policy.decision_model)
            decision = await classifier.classify(
                build_cumulative_session_map_change_request(
                    render_session_map(
                        current_map, max_chars=_SESSION_MAP_RENDER_BUDGET
                    ).text,
                    _render_delta(delta),
                )
            )
            force_reason = _force_reason(frozen, policy)
            decision_data = _decision_diagnostics(
                decision,
                policy=policy,
                force_reason=force_reason,
            )
            if force_reason is None and not _decision_triggers(decision, policy):
                self._map_store.skip_attempt(
                    session_id,
                    vault_name,
                    decision=decision_data,
                )
                logger.info(
                    "session_map_salience_skipped",
                    data={
                        "event": "session_map_salience_skipped",
                        "status": "skipped",
                        "session_id": session_id,
                        "vault_name": vault_name,
                        "through_sequence_index": (
                            frozen.frozen_through_sequence_index
                        ),
                        "pending_turn_count": frozen.frozen_pending_turn_count,
                        "pending_token_count": frozen.frozen_pending_token_count,
                    },
                )
                return ExecutionTaskRunOutcome(
                    value={
                        "status": "salience_stable",
                        "through_sequence_index": (
                            frozen.frozen_through_sequence_index
                        ),
                    },
                    status=ExecutionTaskStatus.SKIPPED,
                    reason="session_memory_salience_stable",
                )

            request = SessionMapAuthoringRequest(
                current_map=current_map,
                delta=delta,
                observed_source_content_revision=(
                    _required_int(frozen.frozen_source_content_revision)
                ),
            )
            authored = await self._author(
                model_alias=policy.author_model,
                request=request,
                created_at=self._now(),
            )
            authoring_data = _authoring_diagnostics(authored)
            map_values = authored.session_map.model_dump(mode="python")
            map_values.update(
                prompt_contract_version=SESSION_MAP_AUTHORING_PROMPT_VERSION,
                decision_model=decision.resolved_model_name,
                authoring_model=authored.resolved_model_name,
            )
            committed_map = SessionMap.model_validate(map_values)
            self._map_store.commit_revision(
                session_id=session_id,
                vault_name=vault_name,
                expected_revision=current_map.revision,
                session_map=committed_map,
                operations=authored.patch_set.operations,
                decision=decision_data,
                authoring=authoring_data,
            )
            logger.info(
                "session_map_revision_committed",
                data={
                    "event": "session_map_revision_committed",
                    "status": "completed",
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "revision": committed_map.revision,
                    "through_sequence_index": (
                        committed_map.updated_through_sequence_index
                    ),
                    "force_reason": force_reason,
                    "operation_count": len(authored.patch_set.operations),
                },
            )
            return ExecutionTaskRunOutcome(
                value={
                    "status": "committed",
                    "revision": committed_map.revision,
                    "through_sequence_index": (
                        committed_map.updated_through_sequence_index
                    ),
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - domain failure becomes task result
            if frozen is not None:
                self._map_store.fail_attempt(
                    session_id,
                    vault_name,
                    error_type=type(exc).__name__,
                    retryable=True,
                    decision=decision_data,
                    authoring=authoring_data,
                )
            logger.warning(
                "session_map_maintenance_failed",
                data={
                    "event": "session_map_maintenance_failed",
                    "status": "failed",
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "error_type": type(exc).__name__,
                    "issue": "session_map_maintenance",
                },
            )
            return ExecutionTaskRunOutcome(
                value={"status": "failed", "error_type": type(exc).__name__},
                status=ExecutionTaskStatus.FAILED,
                reason="session_memory_failed",
                error_type=type(exc).__name__,
            )

    def _load_delta(
        self,
        session_id: str,
        vault_name: str,
        frozen: SessionMapMaintenanceState,
    ) -> tuple[CanonicalMapMessage, ...]:
        start = _required_int(frozen.frozen_from_sequence_index)
        through = _required_int(frozen.frozen_through_sequence_index)
        rows = self._chat_store.get_stored_messages_range(
            session_id,
            vault_name,
            after_sequence_index=start - 1,
            through_sequence_index=through,
        )
        delta = tuple(
            project_canonical_map_message(
                sequence_index=row.sequence_index,
                stored_role=row.role,
                message_json=row.message_json,
            )
            for row in rows
        )
        if (
            not delta
            or delta[0].sequence_index != start
            or delta[-1].sequence_index != through
        ):
            raise ValueError("frozen session-map source range is incomplete")
        return delta

    async def _release_frozen_attempt(
        self,
        session_id: str,
        vault_name: str,
        *,
        error_type: str,
        retryable: bool,
    ) -> None:
        state = self._map_store.get_maintenance_state(session_id, vault_name)
        if state is None or state.status != "processing":
            return
        self._map_store.fail_attempt(
            session_id,
            vault_name,
            error_type=error_type,
            retryable=retryable,
        )


def _eligible(state: SessionMapMaintenanceState, policy: SessionMemoryPolicy) -> bool:
    if state.status == "processing" or state.pending_turn_count <= 0:
        return False
    unchecked_turns = max(
        0,
        state.pending_turn_count - state.decision_checked_pending_turn_count,
    )
    return (
        unchecked_turns >= policy.eligibility_turns
        or state.pending_turn_count >= policy.max_pending_turns
        or state.pending_token_count >= policy.max_pending_tokens
    )


def _force_reason(
    state: SessionMapMaintenanceState, policy: SessionMemoryPolicy
) -> str | None:
    if _required_int(state.frozen_pending_token_count) >= policy.max_pending_tokens:
        return "pending_token_ceiling"
    if _required_int(state.frozen_pending_turn_count) >= policy.max_pending_turns:
        return "pending_turn_ceiling"
    return None


def _decision_triggers(
    decision: DecisionResult[SessionMapCumulativeChangeSignals],
    policy: SessionMemoryPolicy,
) -> bool:
    scores = decision.output.model_dump()
    broad = float(scores["reconciliation_needed"])
    field_max = max(
        float(scores[field])
        for field in SESSION_MAP_CUMULATIVE_CHANGE_FIELDS
        if field != "reconciliation_needed"
    )
    return (
        broad >= policy.broad_change_threshold
        or field_max >= policy.field_change_threshold
    )


def _decision_diagnostics(
    decision: DecisionResult[SessionMapCumulativeChangeSignals],
    *,
    policy: SessionMemoryPolicy,
    force_reason: str | None,
) -> dict[str, JsonValue]:
    return {
        "prompt_contract_version": SESSION_CUMULATIVE_CHANGE_PROMPT_CONTRACT_VERSION,
        "requested_model_alias": decision.requested_model_alias,
        "resolved_model_name": decision.resolved_model_name,
        "provider_name": decision.provider_name,
        "scores": decision.output.model_dump(mode="json"),
        "broad_change_threshold": policy.broad_change_threshold,
        "field_change_threshold": policy.field_change_threshold,
        "force_reason": force_reason,
        "latency_seconds": decision.latency_seconds,
        "requests": decision.usage.requests,
        "input_tokens": decision.usage.input_tokens,
        "output_tokens": decision.usage.output_tokens,
    }


def _authoring_diagnostics(
    authored: SessionMapAuthoringResult,
) -> dict[str, JsonValue]:
    return {
        "prompt_contract_version": SESSION_MAP_AUTHORING_PROMPT_VERSION,
        "requested_model_alias": authored.requested_model_alias,
        "requested_thinking": authored.requested_thinking,
        "resolved_model_name": authored.resolved_model_name,
        "provider_name": authored.provider_name,
        "latency_seconds": authored.latency_seconds,
        "requests": authored.requests,
        "input_tokens": authored.input_tokens,
        "output_tokens": authored.output_tokens,
        "operation_count": len(authored.patch_set.operations),
    }


def _render_delta(delta: tuple[CanonicalMapMessage, ...]) -> str:
    return "\n\n".join(
        f"[{message.sequence_index}:{message.role}]\n{message.content}"
        for message in delta
    )


def _required_int(value: int | None) -> int:
    if value is None:
        raise ValueError("frozen session-map attempt is incomplete")
    return value
