"""Chat history compaction service."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.chat.tool_history import analyze_tool_history
from core.constants import (
    CHAT_HISTORY_COMPACTION_INSTRUCTION,
    CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
    CHAT_HISTORY_RECOVERY_CARD_PREAMBLE,
    SESSION_MAP_CONTEXT_PROMPT_VERSION,
)
from core.identity import ExecutionAuthority
from core.llm.model_factory import build_model_instance
from core.llm.thinking import thinking_value_to_label
from core.logger import UnifiedLogger
from core.memory.session_map.checkpoints import (
    SessionMapCheckpointResult,
    commit_session_map_context_checkpoint,
    load_session_map_checkpoint,
    load_session_map_pending_evidence,
)
from core.memory.session_map.evidence import (
    SessionMapEvidence,
    SessionMapMessageEvidence,
    SessionMapRetrievedEvidence,
    build_session_map_evidence,
    has_contiguous_canonical_sequences,
    resolve_previous_map_excluded_sources,
    resolve_session_map_evidence_range,
)
from core.memory.session_map.models import SessionMapDraft
from core.memory.session_map.readiness import (
    SessionMapCompactionReadiness,
    evaluate_compaction_author_readiness,
    evaluate_session_map_compaction_readiness,
    resolve_session_compaction_strategy,
)
from core.memory.session_map.retained_evidence import (
    project_retained_session_map_evidence,
    project_retrieved_session_map_evidence,
)
from core.memory.session_map.service import (
    SessionMapAuthoringRequest,
    run_session_map_authoring,
)
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    ExecutionTaskStatus,
    chat_session_scope,
    compaction_task_label,
    get_current_execution_task,
)
from core.runtime.state import get_runtime_context
from core.runtime.task_runner import ExecutionTaskSpec
from core.settings import (
    get_compaction_author_model,
    get_compaction_author_thinking,
    get_compaction_high_watermark_tokens,
    get_compaction_low_watermark_tokens,
    get_compaction_retained_turns,
    get_compaction_type,
)
from core.utils.tokens import estimate_token_count

from .chat_store import ChatStore

logger = UnifiedLogger(tag="chat-compaction")

_MODEL_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_SESSION_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()
_SUMMARY_MARKER = "AssistantMD compacted chat history"


class _CompactionStrategyChangedError(ValueError):
    """The strategy pinned while waiting no longer matches the selected author path."""


class _RecordedAutomaticCompactionFailure(RuntimeError):
    """An owned automatic compaction task has already published its failure."""


def _compaction_log_context(
    event: str, *, session_id: str, vault_name: str
) -> dict[str, str | None]:
    """Correlate domain lifecycle and warning deduplication to the owning task."""
    task = get_current_execution_task()
    task_id = task.task_id if task is not None else None
    return {
        "event": event,
        "session_id": session_id,
        "vault_name": vault_name,
        "task_id": task_id,
        "parent_task_id": task.parent_task_id if task is not None else None,
        "issue": f"{event}:{vault_name}:{session_id}:{task_id or 'unscoped'}",
    }


def _compaction_failure_details(exc: Exception, *, reason: str) -> dict[str, str]:
    """Project controlled diagnostics without exposing provider or transcript text."""
    return {
        "reason": reason,
        "error_type": type(exc).__name__[:100],
        "error": (
            f"Compaction did not complete ({reason}); "
            "inspect the correlated execution task."
        ),
    }


@dataclass(frozen=True)
class ChatHistoryCompactionStatus:
    """Status estimate for one chat session."""

    session_id: str
    vault_name: str
    compaction_type: str
    strategy: str
    messages_before: int
    estimated_tokens_before: int
    compaction_high_watermark_tokens: int
    compaction_low_watermark_tokens: int
    compaction_retained_turns: int
    recommended: bool
    already_compacted: bool
    manual_compaction_available: bool


@dataclass(frozen=True)
class ChatHistoryCompactionResult:
    """Result of rewriting one chat session history."""

    session_id: str
    vault_name: str
    status: str
    messages_before: int
    messages_after: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    kept_recent: int
    summary_message_index: int
    compaction_id: str
    compacted_at: str
    source: str

    def as_api_dict(self) -> dict[str, Any]:
        """Return API/UI-safe result fields."""
        return {
            **asdict(self),
            "strategy": "recovery_card",
            "checkpoint_id": self.compaction_id,
        }

    def as_tool_dict(self) -> dict[str, Any]:
        """Return chat-agent-safe result fields."""
        return self.as_api_dict()


@dataclass(frozen=True)
class SteppedHistoryEvictionPlan:
    """Pure high/low-watermark plan over whole provider-history groups."""

    status: str
    reason: str
    history_revision: int | None
    high_watermark_tokens: int
    low_watermark_tokens: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    message_count_before: int
    evicted_message_count: int
    retained_message_count: int
    group_count: int
    evicted_group_count: int
    retained_prefix_count: int
    eviction_start_index: int
    eviction_end_index: int
    minimum_retained_groups: int


@dataclass(frozen=True)
class CanonicalEvictionEnvelopeResult:
    """Result of resolving a plan against one canonical persisted snapshot."""

    status: str
    reason: str
    history_revision: int
    envelopes: tuple[SessionMapEvidence, ...] = ()


@dataclass(frozen=True)
class SessionMapContextReductionResult:
    """One completed stepped-map context reduction."""

    session_id: str
    vault_name: str
    checkpoint_id: str
    action: str
    authoring_task_id: str | None
    consumed_through_sequence_index: int
    messages_before: int
    messages_after: int
    estimated_tokens_before: int
    estimated_tokens_after: int
    source: str

    def as_api_dict(self) -> dict[str, Any]:
        """Return API/UI-safe result fields."""
        return {
            **asdict(self),
            "strategy": "session_map",
            "status": "completed",
        }

    def as_tool_dict(self) -> dict[str, Any]:
        """Return chat-agent-safe result fields."""
        return self.as_api_dict()


@dataclass(frozen=True)
class ChatContextCompactionUnavailableResult:
    """Stable manual result when no safe context reduction is possible."""

    session_id: str
    vault_name: str
    strategy: str
    reason: str
    messages_before: int
    estimated_tokens_before: int
    source: str

    def as_api_dict(self) -> dict[str, Any]:
        """Return API/UI-safe result fields."""
        return {
            **asdict(self),
            "status": "unavailable",
            "messages_after": self.messages_before,
            "estimated_tokens_after": self.estimated_tokens_before,
            "checkpoint_id": None,
        }

    def as_tool_dict(self) -> dict[str, Any]:
        """Return chat-agent-safe result fields."""
        return self.as_api_dict()


@dataclass(frozen=True)
class _HistoryMessageGroup:
    """One conversational group bounded by user or system input."""

    start_index: int
    end_index: int


@asynccontextmanager
async def chat_session_history_lock(
    *, session_id: str, vault_name: str
) -> AsyncIterator[None]:
    """Serialize canonical history mutation for one chat session."""
    lock = await _get_session_lock(session_id=session_id, vault_name=vault_name)
    async with lock:
        yield


async def get_compaction_status(
    *,
    session_id: str,
    vault_name: str,
    store: ChatStore | None = None,
) -> ChatHistoryCompactionStatus:
    """Return the current compaction status for one chat session."""
    chat_store = store or ChatStore()
    messages = chat_store.get_history(session_id, vault_name) or []
    estimated_tokens = estimate_history_tokens(messages)
    threshold = get_compaction_high_watermark_tokens()
    strategy = resolve_session_compaction_strategy(
        store=chat_store,
        session_id=session_id,
        vault_name=vault_name,
    )
    checkpoint = chat_store.get_latest_context_checkpoint(session_id, vault_name)
    retained_prefix_count = (
        1
        if checkpoint is not None and checkpoint.checkpoint_kind == "session_map"
        else 0
    )
    groups = _group_history_messages(messages[retained_prefix_count:])
    retained_turns = get_compaction_retained_turns()
    low_watermark = get_compaction_low_watermark_tokens()
    manual_available = len(groups) > retained_turns
    if manual_available:
        if strategy == "session_map":
            readiness = evaluate_session_map_compaction_readiness(
                store=chat_store, session_id=session_id, vault_name=vault_name
            )
            manual_available = readiness.enabled
            if manual_available:
                manual_available = (
                    session_map_reduction_unavailability_reason(
                        messages,
                        high_watermark_tokens=readiness.high_watermark_tokens,
                        low_watermark_tokens=readiness.low_watermark_tokens,
                        minimum_retained_groups=readiness.minimum_retained_groups,
                        retained_prefix_count=retained_prefix_count,
                        force=True,
                    )
                    is None
                )
        else:
            manual_available = evaluate_compaction_author_readiness().enabled
    return ChatHistoryCompactionStatus(
        session_id=session_id,
        vault_name=vault_name,
        compaction_type=get_compaction_type(),
        strategy=strategy,
        messages_before=len(messages),
        estimated_tokens_before=estimated_tokens,
        compaction_high_watermark_tokens=threshold,
        compaction_low_watermark_tokens=low_watermark,
        compaction_retained_turns=retained_turns,
        recommended=estimated_tokens >= threshold,
        already_compacted=checkpoint is not None,
        manual_compaction_available=manual_available,
    )


async def run_chat_context_compaction(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str | None = None,
    focus: str | None = None,
    source: ExecutionTaskSource = ExecutionTaskSource.API,
    authority: ExecutionAuthority,
    store: ChatStore | None = None,
    automatic: bool = False,
) -> (
    ChatHistoryCompactionResult
    | SessionMapContextReductionResult
    | ChatContextCompactionUnavailableResult
):
    """Own one compaction task through its durable checkpoint or unavailable result."""
    runtime = get_runtime_context()
    parent_task = get_current_execution_task()
    owned_task_id: str | None = None

    async def run(
        task: ExecutionTaskSnapshot,
    ) -> (
        ChatHistoryCompactionResult
        | SessionMapContextReductionResult
        | ChatContextCompactionUnavailableResult
    ):
        nonlocal owned_task_id
        owned_task_id = task.task_id
        result = await compact_chat_context(
            session_id=session_id,
            vault_name=vault_name,
            vault_path=vault_path,
            focus=focus,
            source=source,
            authority=authority,
            store=store or runtime.chat_store,
            force=not automatic,
        )
        await runtime.task_coordinator.record_result(task.task_id, result.as_api_dict())
        return result

    try:
        result = await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.HISTORY_COMPACTION,
                scope=chat_session_scope(session_id),
                source=source,
                label=compaction_task_label(session_id),
                authority=authority,
                parent_task_id=parent_task.task_id if parent_task is not None else None,
                metadata={
                    "vault": vault_name,
                    "session_id": session_id,
                    "automatic": automatic,
                },
            ),
            run,
        )
    except Exception as exc:
        if automatic and owned_task_id is not None:
            failed_task = await runtime.task_coordinator.get_task(owned_task_id)
            if (
                failed_task is not None
                and failed_task.status == ExecutionTaskStatus.FAILED
            ):
                raise _RecordedAutomaticCompactionFailure(
                    "Automatic compaction failed in its owning execution task"
                ) from exc
        raise
    if not isinstance(
        result,
        ChatHistoryCompactionResult
        | SessionMapContextReductionResult
        | ChatContextCompactionUnavailableResult,
    ):
        raise TypeError("Compaction execution returned an invalid result")
    return result


async def compact_chat_context(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str | None = None,
    focus: str | None = None,
    source: ExecutionTaskSource = ExecutionTaskSource.API,
    authority: ExecutionAuthority,
    store: ChatStore | None = None,
    force: bool = True,
) -> (
    ChatHistoryCompactionResult
    | SessionMapContextReductionResult
    | ChatContextCompactionUnavailableResult
):
    """Compact one session using its pinned strategy and requested watermark policy."""
    chat_store = store or ChatStore()
    readiness = evaluate_session_map_compaction_readiness(
        store=chat_store,
        session_id=session_id,
        vault_name=vault_name,
    )
    if readiness.strategy != "session_map":
        try:
            return await compact_chat_history(
                session_id=session_id,
                vault_name=vault_name,
                vault_path=vault_path,
                focus=focus,
                source=source,
                store=chat_store,
            )
        except _CompactionStrategyChangedError:
            # The failed path has released its lock. An upgrade can pin V2 while
            # this request waits; resolve once without opening another task.
            readiness = evaluate_session_map_compaction_readiness(
                store=chat_store,
                session_id=session_id,
                vault_name=vault_name,
            )
            if readiness.strategy != "session_map":
                raise
    if not readiness.enabled:
        raise ValueError(f"Session-map compaction is unavailable: {readiness.reason}.")
    result = await _run_stepped_session_map_reduction(
        session_id=session_id,
        vault_name=vault_name,
        readiness=readiness,
        authority=authority,
        store=chat_store,
        source=source,
        focus=focus,
        force=force,
    )
    if result is None:
        messages = (
            chat_store.get_history(session_id, vault_name, mode="effective") or []
        )
        return _unavailable_compaction_result(
            session_id=session_id,
            vault_name=vault_name,
            strategy="session_map",
            messages=messages,
            source=source,
        )
    return result


async def compact_chat_history(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str | None = None,
    focus: str | None = None,
    source: ExecutionTaskSource = ExecutionTaskSource.API,
    store: ChatStore | None = None,
) -> ChatHistoryCompactionResult | ChatContextCompactionUnavailableResult:
    """Compact one chat session into a summary plus recent raw messages."""
    chat_store = store or ChatStore()
    source_value = str(source)
    logger.info(
        "chat_compaction_started",
        data={
            **_compaction_log_context(
                "chat_compaction_started", session_id=session_id, vault_name=vault_name
            ),
            "status": "started",
            "source": source_value,
            "focus_provided": bool((focus or "").strip()),
        },
    )
    failure_reason = "history_snapshot_failed"
    try:
        async with chat_session_history_lock(
            session_id=session_id, vault_name=vault_name
        ):
            if (
                chat_store.get_session(session_id=session_id, vault_name=vault_name)
                is None
            ):
                raise LookupError(f"Chat session not found: {session_id}")
            checkpoint = chat_store.get_latest_context_checkpoint(
                session_id, vault_name
            )
            if checkpoint is not None and checkpoint.checkpoint_kind == "session_map":
                raise _CompactionStrategyChangedError(
                    "Recovery-card compaction cannot replace a session-map checkpoint; "
                    "use an explicit context rebuild to change strategies."
                )
            history_revision = chat_store.get_session_history_revision(
                session_id, vault_name
            )
            last_message_sequence_index = chat_store.get_highest_message_sequence_index(
                session_id, vault_name
            )
            messages = chat_store.get_history(session_id, vault_name) or []
            stored_messages = chat_store.get_stored_messages(
                session_id, vault_name, mode="effective"
            )
            integrity = analyze_tool_history(messages)
            if not integrity.ok:
                logger.warning(
                    "chat_compaction_tool_integrity_issue",
                    data={
                        **_compaction_log_context(
                            "chat_compaction_tool_integrity_issue",
                            session_id=session_id,
                            vault_name=vault_name,
                        ),
                        "status": "warning",
                        "reason": "tool_history_integrity_issues",
                        "source": source_value,
                        "tool_history_integrity_status": integrity.status,
                        "tool_call_count": integrity.tool_call_count,
                        "tool_return_count": integrity.tool_return_count,
                        "issue_count": len(integrity.issues),
                        "issue_codes": sorted(
                            {issue.code for issue in integrity.issues}
                        ),
                    },
                )
            retained_turns = get_compaction_retained_turns()
            older_messages, recent_messages = split_history_for_compaction(
                messages,
                retained_turns=retained_turns,
            )
            if not older_messages:
                return _unavailable_compaction_result(
                    session_id=session_id,
                    vault_name=vault_name,
                    strategy="recovery_card",
                    messages=messages,
                    source=source,
                )

            estimated_before = estimate_history_tokens(messages)
            trigger, reason = _compaction_trigger_and_reason(source)
            failure_reason = "author_readiness_failed"
            author = evaluate_compaction_author_readiness()
            if not author.enabled or author.model is None:
                failure_reason = author.reason
                raise ValueError(
                    f"Recovery-card compaction is unavailable: {author.reason}."
                )
            author_model = author.model
            author_thinking = author.thinking
            author_thinking_label = thinking_value_to_label(author_thinking)
            logger.info(
                "chat_compaction_plan_selected",
                data={
                    **_compaction_log_context(
                        "chat_compaction_plan_selected",
                        session_id=session_id,
                        vault_name=vault_name,
                    ),
                    "status": "selected",
                    "source": source_value,
                    "trigger": trigger,
                    "reason": reason,
                    "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
                    "author_model": author_model,
                    "author_thinking": author_thinking_label,
                    "history_mode": "effective",
                    "messages_before": len(messages),
                    "older_messages": len(older_messages),
                    "recent_messages": len(recent_messages),
                    "configured_retained_turns": retained_turns,
                    "estimated_tokens_before": estimated_before,
                    "transcript_export": "manual_only",
                    "tool_history_integrity_status": integrity.status,
                    "tool_history_issue_count": len(integrity.issues),
                    "multi_call_batch_count": integrity.multi_call_batch_count,
                    "multi_return_batch_count": integrity.multi_return_batch_count,
                },
            )

            failure_reason = "authoring_failed"
            summary = await _generate_compaction_summary(
                older_messages=older_messages,
                recent_messages=recent_messages,
                focus=focus,
            )
            if not summary:
                raise ValueError("Compaction summary generation returned empty output.")
            summary_message = build_compaction_summary_message(summary)
            retained_stored_messages = (
                stored_messages[-len(recent_messages) :] if recent_messages else []
            )
            replacement = [
                summary_message,
                *(message.message for message in retained_stored_messages),
            ]
            retained_origins = [
                message.fork_sequence_index for message in retained_stored_messages
            ]
            estimated_after = estimate_history_tokens(replacement)
            compacted_at = datetime.now(UTC).isoformat()
            compaction_id = uuid.uuid4().hex
            metadata_update = {
                "last_compaction": {
                    "compaction_id": compaction_id,
                    "compacted_at": compacted_at,
                    "source": source_value,
                    "trigger": trigger,
                    "reason": reason,
                    "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
                    "compaction_type": get_compaction_type(),
                    "compaction_high_watermark_tokens": get_compaction_high_watermark_tokens(),
                    "compaction_retained_turns": retained_turns,
                    "author_model": author_model,
                    "author_thinking": author_thinking_label,
                    "messages_before": len(messages),
                    "messages_after": len(replacement),
                    "estimated_tokens_before": estimated_before,
                    "estimated_tokens_after": estimated_after,
                    "last_message_sequence_index": last_message_sequence_index,
                }
            }
            checkpoint_metadata = {
                **metadata_update["last_compaction"],
                "history_mode": "effective",
                "raw_messages_preserved": True,
            }
            failure_reason = "checkpoint_commit_failed"
            chat_store.add_compaction_checkpoint(
                session_id=session_id,
                vault_name=vault_name,
                checkpoint_id=compaction_id,
                source=source_value,
                message_count_before=len(messages),
                last_message_sequence_index=last_message_sequence_index,
                summary_message=summary_message,
                replacement_history=replacement,
                replacement_source_sequence_indexes=[None, *retained_origins],
                metadata=checkpoint_metadata,
                metadata_update=metadata_update,
                expected_history_revision=history_revision,
            )
            result = ChatHistoryCompactionResult(
                session_id=session_id,
                vault_name=vault_name,
                status="completed",
                messages_before=len(messages),
                messages_after=len(replacement),
                estimated_tokens_before=estimated_before,
                estimated_tokens_after=estimated_after,
                kept_recent=len(recent_messages),
                summary_message_index=0,
                compaction_id=compaction_id,
                compacted_at=compacted_at,
                source=source_value,
            )
            logger.info(
                "chat_compaction_completed",
                data={
                    **_compaction_log_context(
                        "chat_compaction_completed",
                        session_id=session_id,
                        vault_name=vault_name,
                    ),
                    **result.as_tool_dict(),
                    "trigger": trigger,
                    "reason": reason,
                    "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
                    "history_mode": "effective",
                    "raw_messages_preserved": True,
                    "checkpoint_id": compaction_id,
                    "last_message_sequence_index": last_message_sequence_index,
                    "token_delta": estimated_before - estimated_after,
                },
            )
            return result
    except _CompactionStrategyChangedError:
        logger.info(
            "chat_compaction_strategy_changed",
            data={
                **_compaction_log_context(
                    "chat_compaction_strategy_changed",
                    session_id=session_id,
                    vault_name=vault_name,
                ),
                "status": "deferred",
                "source": source_value,
                "strategy": "session_map",
                "reason": "checkpoint_strategy_changed",
            },
        )
        raise
    except Exception as exc:
        logger.warning(
            "chat_compaction_failed",
            data={
                **_compaction_log_context(
                    "chat_compaction_failed",
                    session_id=session_id,
                    vault_name=vault_name,
                ),
                "status": "failed",
                "source": source_value,
                **_compaction_failure_details(exc, reason=failure_reason),
            },
        )
        raise


def _unavailable_compaction_result(
    *,
    session_id: str,
    vault_name: str,
    strategy: str,
    messages: list[ModelMessage],
    source: ExecutionTaskSource,
) -> ChatContextCompactionUnavailableResult:
    """Complete an explicit no-op without authoring or mutating session history."""
    result = ChatContextCompactionUnavailableResult(
        session_id=session_id,
        vault_name=vault_name,
        strategy=strategy,
        reason="retained_turn_floor",
        messages_before=len(messages),
        estimated_tokens_before=estimate_history_tokens(messages),
        source=source.value,
    )
    logger.info(
        "chat_compaction_unavailable",
        data={
            **_compaction_log_context(
                "chat_compaction_unavailable",
                session_id=session_id,
                vault_name=vault_name,
            ),
            **result.as_api_dict(),
        },
    )
    return result


def estimate_history_tokens(messages: list[ModelMessage]) -> int:
    """Estimate token count from provider-native message JSON."""
    if not messages:
        return 0
    parts = [
        _MODEL_MESSAGE_ADAPTER.dump_json(message).decode("utf-8")
        for message in messages
    ]
    return estimate_token_count("\n".join(parts))


def plan_session_map_reduction(
    messages: list[ModelMessage],
    *,
    high_watermark_tokens: int,
    low_watermark_tokens: int,
    minimum_retained_groups: int,
    history_revision: int | None = None,
    retained_prefix_count: int = 0,
    force: bool = False,
) -> SteppedHistoryEvictionPlan:
    """Apply one watermark policy to status, explicit upgrades, and reduction."""
    high_watermark_tokens, low_watermark_tokens = _reduction_watermarks(
        messages, high_watermark_tokens, low_watermark_tokens, force=force
    )
    return plan_stepped_history_eviction(
        messages,
        high_watermark_tokens=high_watermark_tokens,
        low_watermark_tokens=low_watermark_tokens,
        minimum_retained_groups=minimum_retained_groups,
        history_revision=history_revision,
        retained_prefix_count=retained_prefix_count,
    )


def session_map_reduction_unavailability_reason(
    messages: list[ModelMessage],
    *,
    high_watermark_tokens: int,
    low_watermark_tokens: int,
    minimum_retained_groups: int,
    retained_prefix_count: int = 0,
    force: bool = False,
) -> str | None:
    """Assess the planner's eligibility without selecting an eviction boundary."""
    high_watermark_tokens, low_watermark_tokens = _reduction_watermarks(
        messages, high_watermark_tokens, low_watermark_tokens, force=force
    )
    _, _, reason = _assess_stepped_history_eviction(
        messages,
        high_watermark_tokens=high_watermark_tokens,
        low_watermark_tokens=low_watermark_tokens,
        minimum_retained_groups=minimum_retained_groups,
        retained_prefix_count=retained_prefix_count,
    )
    return reason


def _reduction_watermarks(
    messages: list[ModelMessage], high: int, low: int, *, force: bool
) -> tuple[int, int]:
    if force:
        high = max(1, estimate_history_tokens(messages) - 1)
        low = min(low, high - 1)
    return high, low


def _assess_stepped_history_eviction(
    messages: list[ModelMessage],
    *,
    high_watermark_tokens: int,
    low_watermark_tokens: int,
    minimum_retained_groups: int,
    retained_prefix_count: int,
) -> tuple[int, list[_HistoryMessageGroup], str | None]:
    """Share all no-op conditions between eligibility and full planning."""
    _validate_eviction_watermarks(
        high_watermark_tokens=high_watermark_tokens,
        low_watermark_tokens=low_watermark_tokens,
    )
    if minimum_retained_groups < 1:
        raise ValueError("Minimum retained groups must be at least one.")
    if retained_prefix_count < 0 or retained_prefix_count >= len(messages):
        if retained_prefix_count != 0 or messages:
            raise ValueError("Retained prefix must leave evictable history")
    estimated = estimate_history_tokens(messages)
    evictable = messages[retained_prefix_count:]
    groups = _group_history_messages(evictable)
    if estimated < high_watermark_tokens:
        return estimated, groups, "below_high_watermark"
    if not analyze_tool_history(evictable).ok:
        return estimated, groups, "invalid_tool_history"
    if len(groups) <= minimum_retained_groups:
        return estimated, groups, "minimum_retained_groups"
    return estimated, groups, None


def plan_stepped_history_eviction(
    messages: list[ModelMessage],
    *,
    high_watermark_tokens: int,
    low_watermark_tokens: int,
    minimum_retained_groups: int,
    history_revision: int | None = None,
    retained_prefix_count: int = 0,
) -> SteppedHistoryEvictionPlan:
    """Plan safe oldest-group eviction while preserving a recent group floor."""
    estimated_before, groups, unavailable_reason = _assess_stepped_history_eviction(
        messages,
        high_watermark_tokens=high_watermark_tokens,
        low_watermark_tokens=low_watermark_tokens,
        minimum_retained_groups=minimum_retained_groups,
        retained_prefix_count=retained_prefix_count,
    )
    if unavailable_reason is not None:
        return _no_op_eviction_plan(
            reason=unavailable_reason,
            messages=messages,
            groups=groups,
            estimated_tokens=estimated_before,
            history_revision=history_revision,
            high_watermark_tokens=high_watermark_tokens,
            low_watermark_tokens=low_watermark_tokens,
            retained_prefix_count=retained_prefix_count,
            minimum_retained_groups=minimum_retained_groups,
        )

    eviction_end_index = 0
    evicted_group_count = 0
    estimated_after = estimated_before
    for group in groups[:-minimum_retained_groups]:
        eviction_end_index = retained_prefix_count + group.end_index
        evicted_group_count += 1
        estimated_after = estimate_history_tokens(
            [
                *messages[:retained_prefix_count],
                *messages[eviction_end_index:],
            ]
        )
        if estimated_after <= low_watermark_tokens:
            break

    target_reached = estimated_after <= low_watermark_tokens
    return SteppedHistoryEvictionPlan(
        status="planned",
        reason=(
            "low_watermark_reached"
            if target_reached
            else "retained_group_floor_exceeds_low_watermark"
        ),
        history_revision=history_revision,
        high_watermark_tokens=high_watermark_tokens,
        low_watermark_tokens=low_watermark_tokens,
        estimated_tokens_before=estimated_before,
        estimated_tokens_after=estimated_after,
        message_count_before=len(messages),
        evicted_message_count=eviction_end_index - retained_prefix_count,
        retained_message_count=(
            retained_prefix_count + len(messages) - eviction_end_index
        ),
        group_count=len(groups),
        evicted_group_count=evicted_group_count,
        retained_prefix_count=retained_prefix_count,
        eviction_start_index=retained_prefix_count,
        eviction_end_index=eviction_end_index,
        minimum_retained_groups=minimum_retained_groups,
    )


def build_canonical_eviction_envelopes(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    plan: SteppedHistoryEvictionPlan,
) -> CanonicalEvictionEnvelopeResult:
    """Resolve one plan to canonical ranges without accepting synthetic history."""
    history_revision = store.get_session_history_revision(session_id, vault_name)
    if plan.status != "planned" or plan.eviction_end_index <= 0:
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="eviction_not_planned",
            history_revision=history_revision,
        )
    checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
    if checkpoint is not None and checkpoint.checkpoint_kind != "session_map":
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="synthetic_compaction_history",
            history_revision=history_revision,
        )
    expected_prefix_count = 1 if checkpoint is not None else 0
    if plan.retained_prefix_count != expected_prefix_count:
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="invalid_retained_prefix",
            history_revision=history_revision,
        )
    if plan.history_revision is None or plan.history_revision != history_revision:
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="stale_history_revision",
            history_revision=history_revision,
        )

    stored_messages = store.get_stored_messages(
        session_id, vault_name, mode="effective"
    )
    model_messages = store.get_history(session_id, vault_name, mode="effective") or []
    if (
        len(stored_messages) != plan.message_count_before
        or len(model_messages) != plan.message_count_before
        or estimate_history_tokens(model_messages) != plan.estimated_tokens_before
    ):
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="stale_history_snapshot",
            history_revision=history_revision,
        )

    groups = _group_history_messages(model_messages[plan.retained_prefix_count :])
    evicted_groups = [
        group
        for group in groups
        if plan.retained_prefix_count + group.end_index <= plan.eviction_end_index
    ]
    if (
        not evicted_groups
        or plan.retained_prefix_count + evicted_groups[-1].end_index
        != plan.eviction_end_index
        or len(evicted_groups) != plan.evicted_group_count
    ):
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="stale_eviction_boundary",
            history_revision=history_revision,
        )

    envelopes: list[SessionMapEvidence] = []
    for group in evicted_groups:
        group_start = plan.retained_prefix_count + group.start_index
        group_end = plan.retained_prefix_count + group.end_index
        group_stored = stored_messages[group_start:group_end]
        group_models = model_messages[group_start:group_end]
        if not has_contiguous_canonical_sequences(group_stored):
            return CanonicalEvictionEnvelopeResult(
                status="unavailable",
                reason="non_contiguous_canonical_history",
                history_revision=history_revision,
            )
        envelopes.append(
            build_session_map_evidence(
                session_id=session_id,
                vault_name=vault_name,
                history_revision=history_revision,
                stored_messages=group_stored,
                model_messages=group_models,
            )
        )

    return CanonicalEvictionEnvelopeResult(
        status="resolved",
        reason="canonical_ranges_resolved",
        history_revision=history_revision,
        envelopes=tuple(envelopes),
    )


def build_canonical_evidence_range(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    source_start_sequence_index: int,
    source_end_sequence_index: int,
    history_revision: int,
    expected_source_digest: str | None = None,
) -> CanonicalEvictionEnvelopeResult:
    """Rehydrate one canonical raw interval for legacy pending evidence."""
    resolved = resolve_session_map_evidence_range(
        store=store,
        session_id=session_id,
        vault_name=vault_name,
        source_start_sequence_index=source_start_sequence_index,
        source_end_sequence_index=source_end_sequence_index,
        history_revision=history_revision,
        expected_source_digest=expected_source_digest,
    )
    return CanonicalEvictionEnvelopeResult(
        status=resolved.status,
        reason=resolved.reason,
        history_revision=resolved.history_revision,
        envelopes=resolved.evidence,
    )


def _validate_eviction_watermarks(
    *, high_watermark_tokens: int, low_watermark_tokens: int
) -> None:
    if high_watermark_tokens <= 0:
        raise ValueError("High watermark must be greater than zero.")
    if low_watermark_tokens < 0:
        raise ValueError("Low watermark cannot be negative.")
    if low_watermark_tokens >= high_watermark_tokens:
        raise ValueError("Low watermark must be less than high watermark.")


def _group_history_messages(
    messages: list[ModelMessage],
) -> list[_HistoryMessageGroup]:
    if not messages:
        return []
    groups: list[_HistoryMessageGroup] = []
    start_index = 0
    for index in range(1, len(messages)):
        if not _starts_history_group(messages[index]):
            continue
        groups.append(
            _HistoryMessageGroup(
                start_index=start_index,
                end_index=index,
            )
        )
        start_index = index
    groups.append(
        _HistoryMessageGroup(
            start_index=start_index,
            end_index=len(messages),
        )
    )
    return groups


def _starts_history_group(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    return any(
        isinstance(part, UserPromptPart | SystemPromptPart)
        for part in (getattr(message, "parts", ()) or ())
    )


def _no_op_eviction_plan(
    *,
    reason: str,
    messages: list[ModelMessage],
    groups: list[_HistoryMessageGroup],
    estimated_tokens: int,
    history_revision: int | None,
    high_watermark_tokens: int,
    low_watermark_tokens: int,
    retained_prefix_count: int,
    minimum_retained_groups: int,
) -> SteppedHistoryEvictionPlan:
    return SteppedHistoryEvictionPlan(
        status="no_op",
        reason=reason,
        history_revision=history_revision,
        high_watermark_tokens=high_watermark_tokens,
        low_watermark_tokens=low_watermark_tokens,
        estimated_tokens_before=estimated_tokens,
        estimated_tokens_after=estimated_tokens,
        message_count_before=len(messages),
        evicted_message_count=0,
        retained_message_count=len(messages),
        group_count=len(groups),
        evicted_group_count=0,
        retained_prefix_count=retained_prefix_count,
        eviction_start_index=retained_prefix_count,
        eviction_end_index=retained_prefix_count,
        minimum_retained_groups=minimum_retained_groups,
    )


def split_history_for_compaction(
    messages: list[ModelMessage],
    *,
    retained_turns: int,
) -> tuple[list[ModelMessage], list[ModelMessage]]:
    """Split history while retaining complete newest conversational turns."""
    if retained_turns <= 0:
        return [], list(messages)
    groups = _group_history_messages(messages)
    if retained_turns >= len(groups):
        return [], list(messages)
    start = groups[-retained_turns].start_index
    return list(messages[:start]), list(messages[start:])


def build_compaction_summary_message(summary: str) -> ModelRequest:
    """Build the system-maintained summary message stored after compaction."""
    content = (
        f"{_SUMMARY_MARKER}\n\n"
        f"{CHAT_HISTORY_RECOVERY_CARD_PREAMBLE}\n\n"
        f"{summary.strip()}"
    )
    return ModelRequest(parts=[SystemPromptPart(content=content)])


def _compaction_trigger_and_reason(source: ExecutionTaskSource) -> tuple[str, str]:
    """Return stable audit labels for why compaction ran."""
    if source == ExecutionTaskSource.SYSTEM:
        return "auto", "token_threshold"
    if source == ExecutionTaskSource.TOOL:
        return "manual", "agent_tool_requested"
    if source == ExecutionTaskSource.API:
        return "manual", "api_requested"
    return "manual", f"{source.value}_requested"


async def maybe_auto_compact_after_turn(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str,
) -> ChatHistoryCompactionResult | SessionMapContextReductionResult | None:
    """Run the configured automatic context reduction after a completed turn."""
    runtime = get_runtime_context()
    status = await get_compaction_status(
        session_id=session_id, vault_name=vault_name, store=runtime.chat_store
    )
    if status.compaction_type != "auto" or not status.recommended:
        return None
    session = runtime.chat_store.get_session_by_id(session_id)
    if session is None or session.vault_name != vault_name:
        raise LookupError(f"Chat session not found: {session_id}")
    readiness = evaluate_session_map_compaction_readiness(
        store=runtime.chat_store,
        session_id=session_id,
        vault_name=vault_name,
    )
    if readiness.enabled:
        logger.info(
            "compaction_strategy_selected",
            data={
                **_compaction_log_context(
                    "compaction_strategy_selected",
                    session_id=session_id,
                    vault_name=vault_name,
                ),
                "status": "selected",
                "strategy": readiness.strategy,
                "configured_strategy": readiness.configured_strategy,
                "reason": readiness.reason,
                "high_watermark_tokens": readiness.high_watermark_tokens,
                "low_watermark_tokens": readiness.low_watermark_tokens,
                "minimum_retained_groups": readiness.minimum_retained_groups,
                "author_model": readiness.author_model,
            },
        )
    elif readiness.strategy == "session_map":
        logger.info(
            "compaction_strategy_selected",
            data={
                **_compaction_log_context(
                    "compaction_strategy_selected",
                    session_id=session_id,
                    vault_name=vault_name,
                ),
                "status": "deferred",
                "strategy": readiness.strategy,
                "configured_strategy": readiness.configured_strategy,
                "reason": readiness.reason,
                "action": "deferred",
            },
        )
        return None
    try:
        result = await run_chat_context_compaction(
            session_id=session_id,
            vault_name=vault_name,
            vault_path=vault_path,
            authority=ExecutionAuthority(session.owner_principal_id),
            source=ExecutionTaskSource.SYSTEM,
            store=runtime.chat_store,
            automatic=True,
        )
        return (
            None
            if isinstance(result, ChatContextCompactionUnavailableResult)
            else result
        )
    except _RecordedAutomaticCompactionFailure:
        # The operation owns its correlated terminal failure event. Preserve the
        # completed chat turn without repeating that failure in the executor.
        return None


async def _run_stepped_session_map_reduction(
    *,
    session_id: str,
    vault_name: str,
    readiness: SessionMapCompactionReadiness,
    authority: ExecutionAuthority,
    store: ChatStore,
    source: ExecutionTaskSource = ExecutionTaskSource.SYSTEM,
    focus: str | None = None,
    force: bool = False,
) -> SessionMapContextReductionResult | None:
    """Own one correlated terminal lifecycle for V2 reduction."""
    try:
        result = await _execute_stepped_session_map_reduction(
            session_id=session_id,
            vault_name=vault_name,
            readiness=readiness,
            authority=authority,
            store=store,
            source=source,
            focus=focus,
            force=force,
        )
        if result is None:
            logger.info(
                "session_map_context_reduction_skipped",
                data={
                    **_compaction_log_context(
                        "session_map_context_reduction_skipped",
                        session_id=session_id,
                        vault_name=vault_name,
                    ),
                    "status": "skipped",
                    "source": source.value,
                    "strategy": "session_map",
                    "reason": "no_safe_reduction",
                },
            )
        return result
    except asyncio.CancelledError:
        logger.info(
            "session_map_context_reduction_cancelled",
            data={
                **_compaction_log_context(
                    "session_map_context_reduction_cancelled",
                    session_id=session_id,
                    vault_name=vault_name,
                ),
                "status": "cancelled",
                "source": source.value,
                "strategy": "session_map",
                "reason": "reduction_cancelled",
            },
        )
        raise
    except Exception as exc:
        logger.warning(
            "session_map_context_reduction_failed",
            data={
                **_compaction_log_context(
                    "session_map_context_reduction_failed",
                    session_id=session_id,
                    vault_name=vault_name,
                ),
                "status": "failed",
                "source": source.value,
                "strategy": "session_map",
                **_compaction_failure_details(
                    exc, reason="session_map_reduction_failed"
                ),
            },
        )
        raise


async def _execute_stepped_session_map_reduction(
    *,
    session_id: str,
    vault_name: str,
    readiness: SessionMapCompactionReadiness,
    authority: ExecutionAuthority,
    store: ChatStore,
    source: ExecutionTaskSource = ExecutionTaskSource.SYSTEM,
    focus: str | None = None,
    force: bool = False,
) -> SessionMapContextReductionResult | None:
    """Author and commit one map checkpoint while holding the session lock."""
    if not readiness.enabled or readiness.author_model is None:
        raise ValueError("Session-map reduction requires a ready author model")
    logger.info(
        "session_map_context_reduction_started",
        data={
            **_compaction_log_context(
                "session_map_context_reduction_started",
                session_id=session_id,
                vault_name=vault_name,
            ),
            "status": "started",
            "source": source.value,
            "strategy": "session_map",
            "force": force,
            "focus_provided": bool((focus or "").strip()),
        },
    )
    async with chat_session_history_lock(
        session_id=session_id,
        vault_name=vault_name,
    ):
        history_revision = store.get_session_history_revision(session_id, vault_name)
        checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
        if checkpoint is None:
            previous_map = SessionMapDraft()
            retained_prefix_count = 0
            pending_evidence = None
        elif checkpoint.checkpoint_kind == "session_map":
            previous_map = load_session_map_checkpoint(checkpoint)
            retained_prefix_count = 1
            pending_evidence = load_session_map_pending_evidence(checkpoint)
        else:
            raise ValueError("Recovery-card history is not eligible for stepped maps")

        messages = store.get_history(session_id, vault_name, mode="effective") or []
        plan = plan_session_map_reduction(
            messages,
            high_watermark_tokens=readiness.high_watermark_tokens,
            low_watermark_tokens=readiness.low_watermark_tokens,
            minimum_retained_groups=readiness.minimum_retained_groups,
            history_revision=history_revision,
            retained_prefix_count=retained_prefix_count,
            force=force,
        )
        if plan.status != "planned":
            if plan.reason in {"below_high_watermark", "minimum_retained_groups"}:
                return None
            raise ValueError(f"Stepped eviction unavailable: {plan.reason}")
        evidence = build_canonical_eviction_envelopes(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            plan=plan,
        )
        if evidence.status != "resolved" or not evidence.envelopes:
            raise ValueError(
                f"Canonical eviction evidence unavailable: {evidence.reason}"
            )

        cumulative_envelopes = evidence.envelopes
        if pending_evidence is not None:
            rehydrated = build_canonical_evidence_range(
                store=store,
                session_id=session_id,
                vault_name=vault_name,
                source_start_sequence_index=(pending_evidence.start_sequence_index),
                source_end_sequence_index=pending_evidence.end_sequence_index,
                history_revision=history_revision,
                expected_source_digest=pending_evidence.source_digest,
            )
            if rehydrated.status != "resolved" or len(rehydrated.envelopes) != 1:
                raise ValueError(
                    f"Pending session-map evidence unavailable: {rehydrated.reason}"
                )
            if (
                rehydrated.envelopes[0].source_end_sequence_index + 1
                != evidence.envelopes[0].source_start_sequence_index
            ):
                raise ValueError(
                    "Pending and new session-map evidence are not contiguous"
                )
            cumulative_envelopes = (*rehydrated.envelopes, *evidence.envelopes)

        retained_evidence = _build_retained_session_map_evidence(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            plan=plan,
        )
        retrieved_evidence = _build_retrieved_session_map_evidence(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            plan=plan,
            source_start_sequence_index=cumulative_envelopes[
                0
            ].source_start_sequence_index,
        )
        logger.info(
            "session_map_context_reduction_plan_selected",
            data={
                **_compaction_log_context(
                    "session_map_context_reduction_plan_selected",
                    session_id=session_id,
                    vault_name=vault_name,
                ),
                "status": "selected",
                "source": source.value,
                "strategy": "session_map",
                "reason": plan.reason,
                "author_model": readiness.author_model,
                "history_revision": history_revision,
                "messages_before": plan.message_count_before,
                "evicted_message_count": plan.evicted_message_count,
                "retained_message_count": plan.retained_message_count,
                "estimated_tokens_before": plan.estimated_tokens_before,
            },
        )
        authored = await run_session_map_authoring(
            SessionMapAuthoringRequest(
                session_id=session_id,
                vault_name=vault_name,
                model_alias=readiness.author_model,
                thinking=readiness.author_thinking,
                previous_map=previous_map,
                new_evidence=cumulative_envelopes,
                recent_evidence=retained_evidence,
                retrieved_evidence=retrieved_evidence.messages,
                retrieved_evidence_truncated=retrieved_evidence.truncated,
                excluded_source_ranges=resolve_previous_map_excluded_sources(
                    store=store,
                    session_id=session_id,
                    vault_name=vault_name,
                    previous_map=previous_map,
                ),
                focus=focus,
            ),
            authority=authority,
            source=source,
        )
        if (
            store.get_session_history_revision(session_id, vault_name)
            != history_revision
        ):
            raise ValueError("Session history changed during map authoring")
        committed: SessionMapCheckpointResult = commit_session_map_context_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            draft=authored.draft,
            previous_map=previous_map,
            new_evidence=cumulative_envelopes,
            expected_history_revision=history_revision,
            message_count_before=plan.message_count_before,
            source=source.value,
            authoring_task_id=authored.task_id,
            authoring_prompt_version=authored.prompt_contract_version,
            author_model_alias=authored.model_alias,
            author_thinking=authored.thinking,
            recent_evidence=retained_evidence,
            retrieved_evidence=retrieved_evidence.messages,
        )
        messages_after = (
            store.get_history(session_id, vault_name, mode="effective") or []
        )
        estimated_after = estimate_history_tokens(messages_after)
        result = SessionMapContextReductionResult(
            session_id=session_id,
            vault_name=vault_name,
            checkpoint_id=committed.checkpoint.checkpoint_id,
            action="authored",
            authoring_task_id=authored.task_id,
            consumed_through_sequence_index=(
                committed.checkpoint.last_message_sequence_index
            ),
            messages_before=plan.message_count_before,
            messages_after=len(messages_after),
            estimated_tokens_before=plan.estimated_tokens_before,
            estimated_tokens_after=estimated_after,
            source=source.value,
        )
        logger.info(
            "session_map_context_reduction_completed",
            data={
                **_compaction_log_context(
                    "session_map_context_reduction_completed",
                    session_id=session_id,
                    vault_name=vault_name,
                ),
                "status": "completed",
                **asdict(result),
                "prompt_contract_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
                "entry_count": len(authored.draft.entries),
                "trajectory_char_count": (
                    len(authored.draft.trajectory.text)
                    if authored.draft.trajectory is not None
                    else 0
                ),
                "minimum_retained_groups": plan.minimum_retained_groups,
                "retained_group_count": plan.group_count - plan.evicted_group_count,
                "raw_messages_preserved": True,
                "focus_provided": bool((focus or "").strip()),
            },
        )
        return result


def _build_retained_session_map_evidence(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    plan: SteppedHistoryEvictionPlan,
) -> tuple[SessionMapMessageEvidence, ...]:
    """Project the canonical retained suffix as citable authoring evidence."""
    stored = store.get_stored_messages(session_id, vault_name, mode="effective")
    return project_retained_session_map_evidence(stored[plan.eviction_end_index :])


def _build_retrieved_session_map_evidence(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    plan: SteppedHistoryEvictionPlan,
    source_start_sequence_index: int | None = None,
) -> SessionMapRetrievedEvidence:
    """Verify bounded transcript fragments from the full canonical active tail."""
    if source_start_sequence_index is not None:
        stored = store.get_stored_messages_range(
            session_id,
            vault_name,
            after_sequence_index=source_start_sequence_index - 1,
            through_sequence_index=store.get_highest_message_sequence_index(
                session_id, vault_name
            ),
        )
    else:
        stored = store.get_stored_messages(session_id, vault_name, mode="effective")[
            plan.retained_prefix_count :
        ]
    return project_retrieved_session_map_evidence(
        store=store,
        session_id=session_id,
        vault_name=vault_name,
        retained=stored,
    )


async def _get_session_lock(*, session_id: str, vault_name: str) -> asyncio.Lock:
    key = (vault_name, session_id)
    async with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SESSION_LOCKS[key] = lock
        return lock


async def _generate_compaction_summary(
    *,
    older_messages: list[ModelMessage],
    recent_messages: list[ModelMessage],
    focus: str | None,
) -> str:
    from core.llm.agents import collect_response, create_agent

    author_model = get_compaction_author_model()
    if author_model is None:
        raise ValueError("Compaction author model is not configured.")
    author_thinking = get_compaction_author_thinking()
    prompt = _build_summary_prompt(
        older_messages=older_messages,
        recent_messages=recent_messages,
        focus=focus,
    )
    agent = await create_agent(
        model=build_model_instance(author_model, thinking=author_thinking)
    )
    result = await collect_response(agent, prompt)
    return str(result.output or "").strip()


def _build_summary_prompt(
    *,
    older_messages: list[ModelMessage],
    recent_messages: list[ModelMessage] | None = None,
    focus: str | None,
) -> str:
    focus_text = (focus or "").strip()
    payload = {
        "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
        "base_instruction": CHAT_HISTORY_COMPACTION_INSTRUCTION,
        "user_focus": focus_text or None,
        "older_history": [
            _message_to_compaction_source(message) for message in older_messages
        ],
        "retained_recent_history": [
            _message_to_compaction_source(message)
            for message in (recent_messages or [])
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _message_to_compaction_source(message: ModelMessage) -> dict[str, Any]:
    role = "assistant" if isinstance(message, ModelResponse) else "user"
    if _is_compaction_summary_message(message):
        role = "system"
    return {
        "role": role,
        "message_type": type(message).__name__,
        "content": _render_message_text(message),
    }


def _is_compaction_summary_message(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, SystemPromptPart):
            content = getattr(part, "content", "")
            if isinstance(content, str) and content.startswith(_SUMMARY_MARKER):
                return True
    return False


def _render_message_text(message: ModelMessage) -> str:
    rendered: list[str] = []
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolCallPart):
            rendered.append(f"[tool call] {getattr(part, 'tool_name', 'tool')}")
        elif isinstance(part, ToolReturnPart):
            rendered.append(_render_tool_return_for_compaction(part))
        else:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                rendered.append(content)
    return "\n".join(rendered).strip()


def _render_tool_return_for_compaction(part: ToolReturnPart) -> str:
    tool_name = getattr(part, "tool_name", "tool")
    outcome = str(getattr(part, "outcome", "success") or "success").strip().lower()
    content = getattr(part, "content", None)
    if outcome in {"failed", "denied"}:
        return f"[tool result omitted] {tool_name}: outcome={outcome}"
    if _is_empty_tool_return_content(content):
        return f"[tool result omitted] {tool_name}: empty result"
    return f"[tool result] {tool_name}: {content}"


def _is_empty_tool_return_content(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list | tuple | dict | set):
        return len(content) == 0
    return False
