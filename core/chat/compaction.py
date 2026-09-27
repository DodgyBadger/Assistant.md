"""Chat history compaction service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, cast

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
from core.logger import UnifiedLogger
from core.memory.session_map.checkpoints import (
    SessionMapCheckpointResult,
    commit_session_map_checkpoint,
    load_session_map_checkpoint,
)
from core.memory.session_map.models import SessionMapDraft
from core.memory.session_map.readiness import (
    SessionMapReadiness,
    evaluate_session_map_readiness,
)
from core.memory.session_map.service import (
    SessionMapAuthoringRequest,
    run_session_map_authoring,
)
from core.runtime.execution_tasks import (
    ExecutionTaskKind,
    ExecutionTaskSource,
    chat_session_scope,
    compaction_task_label,
)
from core.runtime.state import get_runtime_context, has_runtime_context
from core.runtime.task_runner import ExecutionTaskSpec
from core.settings import (
    get_compaction_keep_recent,
    get_compaction_token_threshold,
    get_compaction_type,
)
from core.utils.tokens import estimate_token_count

from .chat_store import ChatStore, StoredChatMessage

logger = UnifiedLogger(tag="chat-compaction")

_MODEL_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
_SESSION_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}
_SESSION_LOCKS_GUARD = asyncio.Lock()
_SUMMARY_MARKER = "AssistantMD compacted chat history"


@dataclass(frozen=True)
class ChatHistoryCompactionStatus:
    """Status estimate for one chat session."""

    session_id: str
    vault_name: str
    compaction_type: str
    messages_before: int
    estimated_tokens_before: int
    compaction_token_threshold: int
    compaction_keep_recent: int
    recommended: bool
    already_compacted: bool


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
        return asdict(self)

    def as_tool_dict(self) -> dict[str, Any]:
        """Return chat-agent-safe result fields."""
        return asdict(self)


@dataclass(frozen=True)
class SteppedHistoryEvictionPlan:
    """Pure high/low-watermark plan over complete provider-history groups."""

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


@dataclass(frozen=True)
class CanonicalEvictionEnvelope:
    """Immutable projected evidence for one canonical evicted history group."""

    envelope_id: str
    session_id: str
    vault_name: str
    history_revision: int
    source_start_sequence_index: int
    source_end_sequence_index: int
    message_count: int
    estimated_tokens: int
    projected_text: str
    source_digest: str


@dataclass(frozen=True)
class CanonicalEvictionEnvelopeResult:
    """Result of resolving a plan against one canonical persisted snapshot."""

    status: str
    reason: str
    history_revision: int
    envelopes: tuple[CanonicalEvictionEnvelope, ...] = ()


@dataclass(frozen=True)
class SessionMapContextReductionResult:
    """One completed stepped-map context reduction."""

    session_id: str
    vault_name: str
    checkpoint_id: str
    authoring_task_id: str
    consumed_through_sequence_index: int
    messages_before: int
    messages_after: int
    estimated_tokens_before: int
    estimated_tokens_after: int


@dataclass(frozen=True)
class _HistoryMessageGroup:
    """One complete or incomplete conversational group in effective history."""

    start_index: int
    end_index: int
    complete: bool


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
    threshold = get_compaction_token_threshold()
    metadata = chat_store.get_session_metadata(session_id, vault_name)
    return ChatHistoryCompactionStatus(
        session_id=session_id,
        vault_name=vault_name,
        compaction_type=get_compaction_type(),
        messages_before=len(messages),
        estimated_tokens_before=estimated_tokens,
        compaction_token_threshold=threshold,
        compaction_keep_recent=get_compaction_keep_recent(),
        recommended=estimated_tokens >= threshold,
        already_compacted=bool(metadata.get("last_compaction")),
    )


async def compact_chat_history(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str | None = None,
    focus: str | None = None,
    source: ExecutionTaskSource = ExecutionTaskSource.API,
    store: ChatStore | None = None,
) -> ChatHistoryCompactionResult:
    """Compact one chat session into a summary plus recent raw messages."""
    chat_store = store or ChatStore()
    source_value = str(source)
    logger.info(
        "chat_compaction_started",
        data={
            "event": "chat_compaction_started",
            "session_id": session_id,
            "vault_name": vault_name,
            "source": source_value,
            "focus_provided": bool((focus or "").strip()),
        },
    )
    try:
        async with chat_session_history_lock(
            session_id=session_id, vault_name=vault_name
        ):
            messages = chat_store.get_history(session_id, vault_name) or []
            integrity = analyze_tool_history(messages)
            if not integrity.ok:
                logger.warning(
                    "chat_compaction_tool_integrity_issue",
                    data={
                        "event": "chat_compaction_tool_integrity_issue",
                        "session_id": session_id,
                        "vault_name": vault_name,
                        "source": source_value,
                        **integrity.to_dict(),
                    },
                )
            if not messages:
                raise ValueError("Cannot compact an empty chat session.")

            keep_recent = get_compaction_keep_recent()
            older_messages, recent_messages = split_history_for_compaction(
                messages,
                keep_recent=keep_recent,
            )
            if not older_messages:
                raise ValueError("Chat session does not have older history to compact.")

            estimated_before = estimate_history_tokens(messages)
            trigger, reason = _compaction_trigger_and_reason(source)
            logger.info(
                "chat_compaction_plan_selected",
                data={
                    "event": "chat_compaction_plan_selected",
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "source": source_value,
                    "trigger": trigger,
                    "reason": reason,
                    "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
                    "history_mode": "effective",
                    "messages_before": len(messages),
                    "older_messages": len(older_messages),
                    "recent_messages": len(recent_messages),
                    "configured_keep_recent": keep_recent,
                    "estimated_tokens_before": estimated_before,
                    "transcript_export": "manual_only",
                    "tool_history_integrity_status": integrity.status,
                    "tool_history_issue_count": len(integrity.issues),
                    "multi_call_batch_count": integrity.multi_call_batch_count,
                    "multi_return_batch_count": integrity.multi_return_batch_count,
                },
            )

            summary = await _generate_compaction_summary(
                older_messages=older_messages,
                recent_messages=recent_messages,
                focus=focus,
            )
            if not summary:
                raise ValueError("Compaction summary generation returned empty output.")
            summary_message = build_compaction_summary_message(summary)
            replacement = [summary_message, *recent_messages]
            estimated_after = estimate_history_tokens(replacement)
            compacted_at = datetime.now(UTC).isoformat()
            compaction_id = uuid.uuid4().hex
            last_message_sequence_index = chat_store.get_highest_message_sequence_index(
                session_id,
                vault_name,
            )
            metadata_update = {
                "last_compaction": {
                    "compaction_id": compaction_id,
                    "compacted_at": compacted_at,
                    "source": source_value,
                    "trigger": trigger,
                    "reason": reason,
                    "prompt_contract_version": CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
                    "compaction_type": get_compaction_type(),
                    "compaction_token_threshold": get_compaction_token_threshold(),
                    "compaction_keep_recent": keep_recent,
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
            chat_store.add_compaction_checkpoint(
                session_id=session_id,
                vault_name=vault_name,
                checkpoint_id=compaction_id,
                source=source_value,
                message_count_before=len(messages),
                last_message_sequence_index=last_message_sequence_index,
                summary_message=summary_message,
                replacement_history=replacement,
                metadata=checkpoint_metadata,
                metadata_update=metadata_update,
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
                    "event": "chat_compaction_completed",
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
    except Exception as exc:
        logger.warning(
            "chat_compaction_failed",
            data={
                "event": "chat_compaction_failed",
                "session_id": session_id,
                "vault_name": vault_name,
                "source": source_value,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise


def estimate_history_tokens(messages: list[ModelMessage]) -> int:
    """Estimate token count from provider-native message JSON."""
    if not messages:
        return 0
    parts = [
        _MODEL_MESSAGE_ADAPTER.dump_json(message).decode("utf-8")
        for message in messages
    ]
    return estimate_token_count("\n".join(parts))


def plan_stepped_history_eviction(
    messages: list[ModelMessage],
    *,
    high_watermark_tokens: int,
    low_watermark_tokens: int,
    history_revision: int | None = None,
    retained_prefix_count: int = 0,
) -> SteppedHistoryEvictionPlan:
    """Plan safe oldest-group eviction after an optional pinned prefix."""
    _validate_eviction_watermarks(
        high_watermark_tokens=high_watermark_tokens,
        low_watermark_tokens=low_watermark_tokens,
    )
    if retained_prefix_count < 0 or retained_prefix_count >= len(messages):
        if retained_prefix_count != 0 or messages:
            raise ValueError("Retained prefix must leave evictable history")
    estimated_before = estimate_history_tokens(messages)
    evictable_messages = messages[retained_prefix_count:]
    groups = _group_history_messages(evictable_messages)
    if estimated_before <= high_watermark_tokens:
        return _no_op_eviction_plan(
            reason="below_high_watermark",
            messages=messages,
            groups=groups,
            estimated_tokens=estimated_before,
            history_revision=history_revision,
            high_watermark_tokens=high_watermark_tokens,
            low_watermark_tokens=low_watermark_tokens,
            retained_prefix_count=retained_prefix_count,
        )

    integrity = analyze_tool_history(evictable_messages)
    if not integrity.ok:
        return _no_op_eviction_plan(
            reason="invalid_tool_history",
            messages=messages,
            groups=groups,
            estimated_tokens=estimated_before,
            history_revision=history_revision,
            high_watermark_tokens=high_watermark_tokens,
            low_watermark_tokens=low_watermark_tokens,
            retained_prefix_count=retained_prefix_count,
        )
    if len(groups) <= 1:
        return _no_op_eviction_plan(
            reason="insufficient_evictable_history",
            messages=messages,
            groups=groups,
            estimated_tokens=estimated_before,
            history_revision=history_revision,
            high_watermark_tokens=high_watermark_tokens,
            low_watermark_tokens=low_watermark_tokens,
            retained_prefix_count=retained_prefix_count,
        )

    eviction_end_index = 0
    evicted_group_count = 0
    estimated_after = estimated_before
    for group in groups[:-1]:
        if not group.complete:
            return _no_op_eviction_plan(
                reason="incomplete_eviction_prefix",
                messages=messages,
                groups=groups,
                estimated_tokens=estimated_before,
                history_revision=history_revision,
                high_watermark_tokens=high_watermark_tokens,
                low_watermark_tokens=low_watermark_tokens,
                retained_prefix_count=retained_prefix_count,
            )
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
            else "newest_group_exceeds_low_watermark"
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

    envelopes: list[CanonicalEvictionEnvelope] = []
    for group in evicted_groups:
        group_start = plan.retained_prefix_count + group.start_index
        group_end = plan.retained_prefix_count + group.end_index
        group_stored = stored_messages[group_start:group_end]
        group_models = model_messages[group_start:group_end]
        if not _has_contiguous_canonical_sequences(group_stored):
            return CanonicalEvictionEnvelopeResult(
                status="unavailable",
                reason="non_contiguous_canonical_history",
                history_revision=history_revision,
            )
        envelopes.append(
            _build_canonical_eviction_envelope(
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
    """Rehydrate one canonical raw interval for cumulative classification."""
    current_revision = store.get_session_history_revision(session_id, vault_name)
    if current_revision != history_revision:
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="stale_history_revision",
            history_revision=current_revision,
        )
    if (
        source_start_sequence_index < 0
        or source_end_sequence_index < source_start_sequence_index
    ):
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="invalid_source_range",
            history_revision=current_revision,
        )
    stored_messages = store.get_stored_messages_range(
        session_id,
        vault_name,
        after_sequence_index=source_start_sequence_index - 1,
        through_sequence_index=source_end_sequence_index,
    )
    if (
        not stored_messages
        or stored_messages[0].sequence_index != source_start_sequence_index
        or stored_messages[-1].sequence_index != source_end_sequence_index
        or not _has_contiguous_canonical_sequences(stored_messages)
    ):
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="canonical_range_unavailable",
            history_revision=current_revision,
        )
    envelope = _build_canonical_eviction_envelope(
        session_id=session_id,
        vault_name=vault_name,
        history_revision=current_revision,
        stored_messages=stored_messages,
        model_messages=[message.message for message in stored_messages],
    )
    if (
        expected_source_digest is not None
        and envelope.source_digest != expected_source_digest
    ):
        return CanonicalEvictionEnvelopeResult(
            status="unavailable",
            reason="source_digest_mismatch",
            history_revision=current_revision,
        )
    return CanonicalEvictionEnvelopeResult(
        status="resolved",
        reason="canonical_range_resolved",
        history_revision=current_revision,
        envelopes=(envelope,),
    )


def _has_contiguous_canonical_sequences(
    messages: list[StoredChatMessage],
) -> bool:
    if not messages:
        return False
    expected = range(
        messages[0].sequence_index,
        messages[0].sequence_index + len(messages),
    )
    return all(
        message.sequence_index == sequence_index
        for message, sequence_index in zip(messages, expected, strict=True)
    )


def _build_canonical_eviction_envelope(
    *,
    session_id: str,
    vault_name: str,
    history_revision: int,
    stored_messages: list[StoredChatMessage],
    model_messages: list[ModelMessage],
) -> CanonicalEvictionEnvelope:
    source_digest = hashlib.sha256(
        "\n".join(message.message_json for message in stored_messages).encode()
    ).hexdigest()
    source_start = stored_messages[0].sequence_index
    source_end = stored_messages[-1].sequence_index
    envelope_id = hashlib.sha256(
        (
            f"{vault_name}\0{session_id}\0{source_start}\0{source_end}\0"
            f"{source_digest}"
        ).encode()
    ).hexdigest()
    projected_text = "\n\n".join(
        (
            f"[source:{message.sequence_index}] {message.role.upper()}:\n"
            f"{message.content_text}"
        )
        for message in stored_messages
    )
    return CanonicalEvictionEnvelope(
        envelope_id=envelope_id,
        session_id=session_id,
        vault_name=vault_name,
        history_revision=history_revision,
        source_start_sequence_index=source_start,
        source_end_sequence_index=source_end,
        message_count=len(stored_messages),
        estimated_tokens=estimate_history_tokens(model_messages),
        projected_text=projected_text,
        source_digest=source_digest,
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
                complete=_history_group_is_complete(messages[start_index:index]),
            )
        )
        start_index = index
    groups.append(
        _HistoryMessageGroup(
            start_index=start_index,
            end_index=len(messages),
            complete=_history_group_is_complete(messages[start_index:]),
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


def _history_group_is_complete(messages: list[ModelMessage]) -> bool:
    if not messages:
        return False
    if len(messages) == 1 and _is_system_only_message(messages[0]):
        return True
    final_message = messages[-1]
    return isinstance(final_message, ModelResponse) and not _tool_call_ids(
        final_message
    )


def _is_system_only_message(message: ModelMessage) -> bool:
    if not isinstance(message, ModelRequest):
        return False
    parts = getattr(message, "parts", ()) or ()
    return bool(parts) and all(isinstance(part, SystemPromptPart) for part in parts)


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
    )


def split_history_for_compaction(
    messages: list[ModelMessage],
    *,
    keep_recent: int,
) -> tuple[list[ModelMessage], list[ModelMessage]]:
    """Split history while preserving tool-call/result pairs in recent history."""
    if keep_recent <= 0 or keep_recent >= len(messages):
        return [], list(messages)
    start = max(0, len(messages) - keep_recent)
    start = _shift_recent_start_for_tool_pairs(messages, start)
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
    status = await get_compaction_status(session_id=session_id, vault_name=vault_name)
    if status.compaction_type != "auto" or not status.recommended:
        return None
    runtime = get_runtime_context() if has_runtime_context() else None
    if runtime is None:
        return await compact_chat_history(
            session_id=session_id,
            vault_name=vault_name,
            vault_path=vault_path,
            source=ExecutionTaskSource.SYSTEM,
        )
    session = runtime.chat_store.get_session_by_id(session_id)
    if session is None or session.vault_name != vault_name:
        raise LookupError(f"Chat session not found: {session_id}")
    readiness = evaluate_session_map_readiness(
        store=runtime.chat_store,
        session_id=session_id,
        vault_name=vault_name,
    )
    if readiness.enabled:
        logger.info(
            "context_reduction_strategy_selected",
            data={
                "event": "context_reduction_strategy_selected",
                "session_id": session_id,
                "vault_name": vault_name,
                "strategy": readiness.strategy,
                "reason": readiness.reason,
                "high_watermark_tokens": readiness.high_watermark_tokens,
                "low_watermark_tokens": readiness.low_watermark_tokens,
                "author_model": readiness.author_model,
            },
        )
        try:
            return await _run_stepped_session_map_reduction(
                session_id=session_id,
                vault_name=vault_name,
                readiness=readiness,
                authority=ExecutionAuthority(session.owner_principal_id),
                store=runtime.chat_store,
            )
        except Exception as exc:
            logger.warning(
                "session_map_context_reduction_fallback",
                data={
                    "event": "session_map_context_reduction_fallback",
                    "session_id": session_id,
                    "vault_name": vault_name,
                    "strategy": readiness.strategy,
                    "reason": "session_map_reduction_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
    elif readiness.strategy == "stepped_session_map":
        logger.info(
            "context_reduction_strategy_selected",
            data={
                "event": "context_reduction_strategy_selected",
                "session_id": session_id,
                "vault_name": vault_name,
                "strategy": "recovery_card",
                "reason": readiness.reason,
                "configured_strategy": readiness.strategy,
            },
        )
    return await _run_automatic_recovery_card_compaction(
        session_id=session_id,
        vault_name=vault_name,
        vault_path=vault_path,
        authority=ExecutionAuthority(session.owner_principal_id),
    )


async def _run_stepped_session_map_reduction(
    *,
    session_id: str,
    vault_name: str,
    readiness: SessionMapReadiness,
    authority: ExecutionAuthority,
    store: ChatStore,
) -> SessionMapContextReductionResult | None:
    """Author and commit one map checkpoint while holding the session lock."""
    if not readiness.enabled or readiness.author_model is None:
        raise ValueError("Session-map reduction requires a ready author model")
    async with chat_session_history_lock(
        session_id=session_id,
        vault_name=vault_name,
    ):
        history_revision = store.get_session_history_revision(session_id, vault_name)
        checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
        if checkpoint is None:
            previous_map = SessionMapDraft()
            retained_prefix_count = 0
        elif checkpoint.checkpoint_kind == "session_map":
            previous_map = load_session_map_checkpoint(checkpoint)
            retained_prefix_count = 1
        else:
            raise ValueError("Recovery-card history is not eligible for stepped maps")

        messages = store.get_history(session_id, vault_name, mode="effective") or []
        plan = plan_stepped_history_eviction(
            messages,
            high_watermark_tokens=readiness.high_watermark_tokens,
            low_watermark_tokens=readiness.low_watermark_tokens,
            history_revision=history_revision,
            retained_prefix_count=retained_prefix_count,
        )
        if plan.status != "planned":
            if plan.reason == "below_high_watermark":
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

        authored = await run_session_map_authoring(
            SessionMapAuthoringRequest(
                session_id=session_id,
                vault_name=vault_name,
                model_alias=readiness.author_model,
                thinking=readiness.author_thinking,
                previous_map=previous_map,
                envelopes=evidence.envelopes,
            ),
            authority=authority,
            source=ExecutionTaskSource.SYSTEM,
        )
        if (
            store.get_session_history_revision(session_id, vault_name)
            != history_revision
        ):
            raise ValueError("Session history changed during map authoring")
        committed: SessionMapCheckpointResult = commit_session_map_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            draft=authored.draft,
            previous_map=previous_map,
            envelopes=evidence.envelopes,
            expected_history_revision=history_revision,
            message_count_before=plan.message_count_before,
            source=ExecutionTaskSource.SYSTEM.value,
            authoring_task_id=authored.task_id,
        )
        messages_after = (
            store.get_history(session_id, vault_name, mode="effective") or []
        )
        estimated_after = estimate_history_tokens(messages_after)
        result = SessionMapContextReductionResult(
            session_id=session_id,
            vault_name=vault_name,
            checkpoint_id=committed.checkpoint.checkpoint_id,
            authoring_task_id=authored.task_id,
            consumed_through_sequence_index=(
                committed.checkpoint.last_message_sequence_index
            ),
            messages_before=plan.message_count_before,
            messages_after=len(messages_after),
            estimated_tokens_before=plan.estimated_tokens_before,
            estimated_tokens_after=estimated_after,
        )
        logger.info(
            "session_map_context_reduction_completed",
            data={
                "event": "session_map_context_reduction_completed",
                **asdict(result),
                "prompt_contract_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
                "entry_count": len(authored.draft.entries),
                "raw_messages_preserved": True,
            },
        )
        return result


async def _run_automatic_recovery_card_compaction(
    *,
    session_id: str,
    vault_name: str,
    vault_path: str,
    authority: ExecutionAuthority,
) -> ChatHistoryCompactionResult:
    """Run the existing recovery-card path through its execution task."""
    runtime = get_runtime_context()
    return cast(
        ChatHistoryCompactionResult,
        await runtime.task_runner.run_inline(
            ExecutionTaskSpec(
                kind=ExecutionTaskKind.HISTORY_COMPACTION,
                scope=chat_session_scope(session_id),
                source=ExecutionTaskSource.SYSTEM,
                label=compaction_task_label(session_id),
                authority=authority,
                metadata={
                    "vault": vault_name,
                    "session_id": session_id,
                    "automatic": True,
                },
            ),
            lambda _task: compact_chat_history(
                session_id=session_id,
                vault_name=vault_name,
                vault_path=vault_path,
                source=ExecutionTaskSource.SYSTEM,
            ),
        ),
    )


async def _get_session_lock(*, session_id: str, vault_name: str) -> asyncio.Lock:
    key = (vault_name, session_id)
    async with _SESSION_LOCKS_GUARD:
        lock = _SESSION_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SESSION_LOCKS[key] = lock
        return lock


def _shift_recent_start_for_tool_pairs(messages: list[ModelMessage], start: int) -> int:
    while start > 0 and _boundary_splits_tool_pair(
        messages[start - 1], messages[start]
    ):
        start -= 1
    while start > 0 and _message_has_tool_return(messages[start]):
        start -= 1
    return start


def _boundary_splits_tool_pair(previous: ModelMessage, current: ModelMessage) -> bool:
    previous_calls = _tool_call_ids(previous)
    current_returns = _tool_return_ids(current)
    return bool(previous_calls & current_returns)


def _tool_call_ids(message: ModelMessage) -> set[str]:
    ids: set[str] = set()
    if not isinstance(message, ModelResponse):
        return ids
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolCallPart):
            tool_call_id = getattr(part, "tool_call_id", None)
            if tool_call_id:
                ids.add(str(tool_call_id))
    return ids


def _tool_return_ids(message: ModelMessage) -> set[str]:
    ids: set[str] = set()
    if not isinstance(message, ModelRequest):
        return ids
    for part in getattr(message, "parts", ()) or ():
        if isinstance(part, ToolReturnPart):
            tool_call_id = getattr(part, "tool_call_id", None)
            if tool_call_id:
                ids.add(str(tool_call_id))
    return ids


def _message_has_tool_return(message: ModelMessage) -> bool:
    return bool(_tool_return_ids(message))


async def _generate_compaction_summary(
    *,
    older_messages: list[ModelMessage],
    recent_messages: list[ModelMessage],
    focus: str | None,
) -> str:
    from core.llm.agents import collect_response, create_agent

    prompt = _build_summary_prompt(
        older_messages=older_messages,
        recent_messages=recent_messages,
        focus=focus,
    )
    agent = await create_agent()
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
