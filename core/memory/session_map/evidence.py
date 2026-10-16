"""Canonical source evidence accepted by session-map authoring."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import TypeGuard

from pydantic import TypeAdapter
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    NativeToolReturnPart,
    ToolReturnPart,
)

from core.chat.chat_store import ChatStore, StoredChatMessage
from core.utils.messages import MessageProjection, project_message
from core.utils.tokens import estimate_token_count

from .models import SessionMapDraft, SourceRange

_MODEL_MESSAGE_ADAPTER: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)
MAX_RETRIEVED_SESSION_MAP_EVIDENCE_TOKENS = 8_000
SESSION_MAP_EVIDENCE_ADMISSION_VERSION = 1


@dataclass(frozen=True)
class SessionMapEvidence:
    """One immutable canonical history range projected for map authoring."""

    evidence_id: str
    session_id: str
    vault_name: str
    history_revision: int
    source_start_sequence_index: int
    source_end_sequence_index: int
    message_count: int
    estimated_tokens: int
    projected_text: str
    source_digest: str
    citable_source_ranges: tuple[SourceRange, ...]

    def __post_init__(self) -> None:
        prior_end = self.source_start_sequence_index - 1
        for source in self.citable_source_ranges:
            if (
                source.start <= prior_end
                or source.start < self.source_start_sequence_index
                or source.end > self.source_end_sequence_index
            ):
                raise ValueError(
                    "Citable evidence ranges must be ordered within consumption"
                )
            prior_end = source.end

    @property
    def envelope_id(self) -> str:
        """Return the compatibility identity used by existing checkpoints."""
        return self.evidence_id


@dataclass(frozen=True)
class SessionMapMessageEvidence:
    """One canonical message or verified character fragment available to the author."""

    sequence_index: int
    role: str
    content_text: str
    content_start: int = 0
    content_end: int | None = None
    content_complete: bool = True

    def __post_init__(self) -> None:
        if self.sequence_index < 0:
            raise ValueError("Session-map evidence sequence index cannot be negative")
        if not self.role.strip():
            raise ValueError("Session-map evidence requires a message role")
        content_end = (
            self.content_start + len(self.content_text)
            if self.content_end is None
            else self.content_end
        )
        if (
            self.content_start < 0
            or content_end < self.content_start
            or content_end - self.content_start != len(self.content_text)
        ):
            raise ValueError("Session-map evidence offsets must match its text")
        if self.content_complete and self.content_start != 0:
            raise ValueError("Complete evidence must start at the canonical beginning")
        object.__setattr__(self, "content_end", content_end)

    def as_authoring_dict(self) -> dict[str, object]:
        """Project source identity and exact fragment bounds for authoring."""
        return {
            "source_range": {"start": self.sequence_index, "end": self.sequence_index},
            "sequence_index": self.sequence_index,
            "role": self.role,
            "content": self.content_text,
            "content_start": self.content_start,
            "content_end": self.content_end,
            "content_complete": self.content_complete,
        }

    @property
    def source_start_sequence_index(self) -> int:
        return self.sequence_index

    @property
    def source_end_sequence_index(self) -> int:
        return self.sequence_index

    @property
    def citable_source_ranges(self) -> tuple[SourceRange, ...]:
        return (SourceRange(start=self.sequence_index, end=self.sequence_index),)


@dataclass(frozen=True)
class SessionMapRetrievedEvidence:
    """Bounded verified fragments with explicit aggregate admission truncation."""

    messages: tuple[SessionMapMessageEvidence, ...] = ()
    truncated: bool = False


def bound_retrieved_session_map_evidence(
    messages: Iterable[SessionMapMessageEvidence],
    *,
    max_tokens: int = MAX_RETRIEVED_SESSION_MAP_EVIDENCE_TOKENS,
) -> SessionMapRetrievedEvidence:
    """Deduplicate fragments and cap their serialized authoring payload."""
    if max_tokens <= 0:
        raise ValueError("Retrieved evidence token budget must be positive")
    accepted: list[SessionMapMessageEvidence] = []
    seen: set[tuple[int, int, int | None]] = set()

    def fits(candidate: SessionMapMessageEvidence) -> bool:
        payload = {
            "retrieved_canonical_evidence": [
                item.as_authoring_dict() for item in (*accepted, candidate)
            ]
        }
        return (
            estimate_token_count(json.dumps(payload, ensure_ascii=False, indent=2))
            <= max_tokens
        )

    for message in messages:
        key = (message.sequence_index, message.content_start, message.content_end)
        if key in seen:
            continue
        seen.add(key)
        if fits(message):
            accepted.append(message)
            continue
        low, high = 1, len(message.content_text)
        best: SessionMapMessageEvidence | None = None
        while low <= high:
            length = (low + high) // 2
            candidate = replace(
                message,
                content_text=message.content_text[:length],
                content_end=message.content_start + length,
                content_complete=False,
            )
            if fits(candidate):
                best = candidate
                low = length + 1
            else:
                high = length - 1
        if best is not None:
            accepted.append(best)
        return SessionMapRetrievedEvidence(tuple(accepted), truncated=True)
    return SessionMapRetrievedEvidence(tuple(accepted))


@dataclass(frozen=True)
class SessionMapEvidenceRangeResult:
    """Result of resolving a canonical range for session-map authoring."""

    status: str
    reason: str
    history_revision: int
    evidence: tuple[SessionMapEvidence, ...] = ()


def is_session_map_retrieval_part(
    part: object,
) -> TypeGuard[ToolReturnPart | NativeToolReturnPart]:
    """Identify retrieval envelopes across provider-native return variants."""
    return (
        isinstance(part, ToolReturnPart | NativeToolReturnPart)
        and part.tool_name == "session_ops"
    )


def contains_session_map_retrieval_result(message: ModelMessage) -> bool:
    """Identify messages whose canonical text includes a retrieval envelope."""
    return any(is_session_map_retrieval_part(part) for part in message.parts)


def project_session_map_message(message: ModelMessage) -> MessageProjection | None:
    """Expose eligible message parts without making retrieval envelopes evidence."""
    if isinstance(message, ModelRequest):
        safe_request_parts = [
            part for part in message.parts if not is_session_map_retrieval_part(part)
        ]
        if not safe_request_parts:
            return None
        projected_message: ModelMessage = replace(message, parts=safe_request_parts)
    elif isinstance(message, ModelResponse):
        safe_response_parts = [
            part for part in message.parts if not is_session_map_retrieval_part(part)
        ]
        if not safe_response_parts:
            return None
        projected_message = replace(message, parts=safe_response_parts)
    else:
        raise TypeError("Unsupported canonical message variant")
    projection = project_message(projected_message)
    return projection if projection.content_text else None


def resolve_previous_map_excluded_sources(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    previous_map: SessionMapDraft,
) -> tuple[SourceRange, ...]:
    """Withhold legacy citations whose messages contain retrieval envelopes."""
    sources = [source for entry in previous_map.entries for source in entry.sources]
    if previous_map.trajectory is not None:
        sources.extend(previous_map.trajectory.sources)
    if not sources:
        return ()
    checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
    metadata = (
        json.loads(checkpoint.metadata_json or "{}") if checkpoint is not None else {}
    )
    admission_version = (
        metadata.get("evidence_admission_version")
        if isinstance(metadata, dict)
        else None
    )
    verified_admission = (
        checkpoint is not None
        and checkpoint.checkpoint_kind == "session_map"
        and isinstance(metadata, dict)
        and metadata.get("map") == previous_map.model_dump(mode="json")
        and type(admission_version) is int
        and admission_version == SESSION_MAP_EVIDENCE_ADMISSION_VERSION
    )
    excluded: set[int] = set()
    checked_through = -1
    for source in sorted(sources, key=lambda item: (item.start, item.end)):
        start = max(source.start, checked_through + 1)
        if start > source.end:
            continue
        for message in store.get_stored_messages_range(
            session_id,
            vault_name,
            after_sequence_index=start - 1,
            through_sequence_index=source.end,
        ):
            if contains_session_map_retrieval_result(message.message) and (
                not verified_admission
                or project_session_map_message(message.message) is None
            ):
                excluded.add(message.sequence_index)
        checked_through = source.end
    return tuple(SourceRange(start=index, end=index) for index in sorted(excluded))


def build_session_map_evidence(
    *,
    session_id: str,
    vault_name: str,
    history_revision: int,
    stored_messages: Sequence[StoredChatMessage],
    model_messages: Sequence[ModelMessage],
) -> SessionMapEvidence:
    """Project one contiguous canonical history range into bounded evidence."""
    if not stored_messages or len(stored_messages) != len(model_messages):
        raise ValueError("Session-map evidence requires aligned canonical messages")
    if not has_contiguous_canonical_sequences(stored_messages):
        raise ValueError("Session-map evidence must be canonically contiguous")
    source_digest = hashlib.sha256(
        "\n".join(message.message_json for message in stored_messages).encode()
    ).hexdigest()
    source_start = stored_messages[0].sequence_index
    source_end = stored_messages[-1].sequence_index
    evidence_id = hashlib.sha256(
        (
            f"{vault_name}\0{session_id}\0{source_start}\0{source_end}\0"
            f"{source_digest}"
        ).encode()
    ).hexdigest()
    projected: list[str] = []
    citable: list[SourceRange] = []
    for message in stored_messages:
        projection = project_session_map_message(message.message)
        if projection is None:
            continue
        projected.append(
            f"[source:{message.sequence_index}] {projection.role.upper()}:\n"
            f"{projection.content_text}"
        )
        if citable and citable[-1].end + 1 == message.sequence_index:
            citable[-1] = SourceRange(
                start=citable[-1].start, end=message.sequence_index
            )
        else:
            citable.append(
                SourceRange(start=message.sequence_index, end=message.sequence_index)
            )
    return SessionMapEvidence(
        evidence_id=evidence_id,
        session_id=session_id,
        vault_name=vault_name,
        history_revision=history_revision,
        source_start_sequence_index=source_start,
        source_end_sequence_index=source_end,
        message_count=len(stored_messages),
        estimated_tokens=estimate_token_count(
            "\n".join(
                _MODEL_MESSAGE_ADAPTER.dump_json(message).decode("utf-8")
                for message in model_messages
            )
        ),
        projected_text="\n\n".join(projected),
        source_digest=source_digest,
        citable_source_ranges=tuple(citable),
    )


def resolve_session_map_evidence_range(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    source_start_sequence_index: int,
    source_end_sequence_index: int,
    history_revision: int,
    expected_source_digest: str | None = None,
) -> SessionMapEvidenceRangeResult:
    """Resolve one canonical raw interval without any compaction assumptions."""
    current_revision = store.get_session_history_revision(session_id, vault_name)
    if current_revision != history_revision:
        return SessionMapEvidenceRangeResult(
            status="unavailable",
            reason="stale_history_revision",
            history_revision=current_revision,
        )
    if (
        source_start_sequence_index < 0
        or source_end_sequence_index < source_start_sequence_index
    ):
        return SessionMapEvidenceRangeResult(
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
        or not has_contiguous_canonical_sequences(stored_messages)
    ):
        return SessionMapEvidenceRangeResult(
            status="unavailable",
            reason="canonical_range_unavailable",
            history_revision=current_revision,
        )
    evidence = build_session_map_evidence(
        session_id=session_id,
        vault_name=vault_name,
        history_revision=current_revision,
        stored_messages=stored_messages,
        model_messages=[message.message for message in stored_messages],
    )
    if (
        expected_source_digest is not None
        and evidence.source_digest != expected_source_digest
    ):
        return SessionMapEvidenceRangeResult(
            status="unavailable",
            reason="source_digest_mismatch",
            history_revision=current_revision,
        )
    return SessionMapEvidenceRangeResult(
        status="resolved",
        reason="canonical_range_resolved",
        history_revision=current_revision,
        evidence=(evidence,),
    )


def has_contiguous_canonical_sequences(
    messages: Sequence[StoredChatMessage],
) -> bool:
    """Return whether stored messages form one canonical sequence interval."""
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
