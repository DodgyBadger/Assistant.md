"""Durable checkpoint composition for eviction-derived session maps."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelRequest, SystemPromptPart

from core.chat.chat_store import ChatStore, StoredContextCheckpoint
from core.constants import (
    SESSION_MAP_CONTEXT_PREAMBLE,
    SESSION_MAP_CONTEXT_PROMPT_VERSION,
)

from .authoring import SessionMapEvidenceEnvelope, SessionMapRetainedEvidence
from .models import SessionMapDraft, validate_session_map_provenance

SESSION_MAP_CONTEXT_MARKER = "AssistantMD session map"


@dataclass(frozen=True)
class SessionMapCheckpointResult:
    """One committed map revision and its effective-history boundary."""

    checkpoint: StoredContextCheckpoint
    draft: SessionMapDraft


@dataclass(frozen=True)
class SessionMapPendingEvidence:
    """Legacy canonical interval awaiting one unconditional map rewrite."""

    start_sequence_index: int
    end_sequence_index: int
    source_digest: str
    estimated_tokens: int

    def __post_init__(self) -> None:
        if self.start_sequence_index < 0:
            raise ValueError("Pending evidence start cannot be negative")
        if self.end_sequence_index < self.start_sequence_index:
            raise ValueError("Pending evidence range is reversed")
        if len(self.source_digest) != 64 or any(
            character not in "0123456789abcdef" for character in self.source_digest
        ):
            raise ValueError("Pending evidence requires a SHA-256 source digest")
        if self.estimated_tokens <= 0:
            raise ValueError("Pending evidence token estimate must be positive")


def build_session_map_context_message(draft: SessionMapDraft) -> ModelRequest:
    """Render a bounded typed map as one provider-history system message."""
    payload = {
        "prompt_contract_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
        "guidance": SESSION_MAP_CONTEXT_PREAMBLE,
        "session_map": draft.model_dump(mode="json"),
    }
    content = (
        f"{SESSION_MAP_CONTEXT_MARKER}\n\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    return ModelRequest(parts=[SystemPromptPart(content=content)])


def commit_session_map_checkpoint(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    draft: SessionMapDraft,
    previous_map: SessionMapDraft,
    envelopes: tuple[SessionMapEvidenceEnvelope, ...],
    expected_history_revision: int,
    message_count_before: int,
    source: str,
    authoring_task_id: str | None = None,
    checkpoint_id: str | None = None,
    retained_evidence: tuple[SessionMapRetainedEvidence, ...] = (),
    map_observed_through_sequence_index: int | None = None,
) -> SessionMapCheckpointResult:
    """Atomically commit one map revision without changing canonical messages."""
    _validate_checkpoint_evidence(
        session_id=session_id,
        vault_name=vault_name,
        envelopes=envelopes,
        expected_history_revision=expected_history_revision,
    )
    validate_session_map_provenance(
        draft,
        envelopes=(*envelopes, *retained_evidence),
        previous_map=previous_map,
    )
    consumed_through = envelopes[-1].source_end_sequence_index
    retained_prior = consumed_through
    for message in retained_evidence:
        if message.sequence_index <= retained_prior:
            raise ValueError("Retained evidence must follow the eviction boundary")
        retained_prior = message.sequence_index
    observed_through = (
        map_observed_through_sequence_index
        if map_observed_through_sequence_index is not None
        else (
            retained_evidence[-1].sequence_index
            if retained_evidence
            else consumed_through
        )
    )
    if observed_through < 0:
        raise ValueError("Session-map observed boundary cannot be negative")
    latest = store.get_latest_context_checkpoint(session_id, vault_name)
    if latest is not None:
        if latest.checkpoint_kind != "session_map":
            raise ValueError(
                "Session-map checkpoints cannot follow recovery-card history"
            )
        if consumed_through <= latest.last_message_sequence_index:
            raise ValueError("Session-map checkpoint boundary must advance")

    resolved_checkpoint_id = checkpoint_id or uuid.uuid4().hex
    context_message = build_session_map_context_message(draft)
    metadata: dict[str, Any] = {
        "checkpoint_kind": "session_map",
        "prompt_contract_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
        "source_history_revision": expected_history_revision,
        "consumed_through_sequence_index": consumed_through,
        "map_observed_through_sequence_index": observed_through,
        "evidence_envelope_ids": [envelope.envelope_id for envelope in envelopes],
        "map": draft.model_dump(mode="json"),
    }
    if authoring_task_id:
        metadata["authoring_task_id"] = authoring_task_id
    store.add_context_checkpoint(
        session_id=session_id,
        vault_name=vault_name,
        checkpoint_id=resolved_checkpoint_id,
        checkpoint_kind="session_map",
        source=source,
        message_count_before=message_count_before,
        last_message_sequence_index=consumed_through,
        summary_message=context_message,
        replacement_history=[context_message],
        metadata=metadata,
        metadata_update={
            "last_session_map_checkpoint": {
                "checkpoint_id": resolved_checkpoint_id,
                "prompt_contract_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
                "consumed_through_sequence_index": consumed_through,
                "map_observed_through_sequence_index": observed_through,
                "source_history_revision": expected_history_revision,
            }
        },
        expected_history_revision=expected_history_revision,
    )
    checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
    if checkpoint is None or checkpoint.checkpoint_id != resolved_checkpoint_id:
        raise RuntimeError("Committed session-map checkpoint could not be reloaded")
    return SessionMapCheckpointResult(checkpoint=checkpoint, draft=draft)


def load_session_map_checkpoint(
    checkpoint: StoredContextCheckpoint,
) -> SessionMapDraft:
    """Load and validate the typed map payload from one stored checkpoint."""
    if checkpoint.checkpoint_kind != "session_map":
        raise ValueError("Context checkpoint is not a session map")
    metadata = _load_session_map_metadata(checkpoint)
    try:
        map_payload = metadata["map"]
    except (KeyError, TypeError) as exc:
        raise ValueError("Session-map checkpoint metadata is invalid") from exc
    return SessionMapDraft.model_validate(map_payload)


def load_session_map_pending_evidence(
    checkpoint: StoredContextCheckpoint,
) -> SessionMapPendingEvidence | None:
    """Load the bounded pending range attached to one map checkpoint."""
    metadata = _load_session_map_metadata(checkpoint)
    payload = metadata.get("pending_evidence")
    if payload is None:
        return None
    try:
        return SessionMapPendingEvidence(**payload)
    except (TypeError, ValueError) as exc:
        raise ValueError("Session-map pending evidence metadata is invalid") from exc


def load_session_map_observed_through(
    checkpoint: StoredContextCheckpoint,
) -> int:
    """Load the newest canonical message seen by the persisted map author."""
    metadata = _load_session_map_metadata(checkpoint)
    value = metadata.get(
        "map_observed_through_sequence_index",
        checkpoint.last_message_sequence_index,
    )
    if not isinstance(value, int) or value < 0:
        raise ValueError("Session-map observed boundary metadata is invalid")
    return value


def _load_session_map_metadata(
    checkpoint: StoredContextCheckpoint,
) -> dict[str, Any]:
    if checkpoint.checkpoint_kind != "session_map":
        raise ValueError("Context checkpoint is not a session map")
    if not checkpoint.metadata_json:
        raise ValueError("Session-map checkpoint is missing metadata")
    try:
        metadata = json.loads(checkpoint.metadata_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Session-map checkpoint metadata is invalid") from exc
    if not isinstance(metadata, dict):
        raise ValueError("Session-map checkpoint metadata is invalid")
    return metadata


def _validate_checkpoint_evidence(
    *,
    session_id: str,
    vault_name: str,
    envelopes: tuple[SessionMapEvidenceEnvelope, ...],
    expected_history_revision: int,
) -> None:
    if not envelopes:
        raise ValueError("Session-map checkpoint requires evidence envelopes")
    prior_end = -1
    for envelope in envelopes:
        if envelope.session_id != session_id or envelope.vault_name != vault_name:
            raise ValueError("Session-map checkpoint evidence scope does not match")
        if envelope.history_revision != expected_history_revision:
            raise ValueError("Session-map checkpoint evidence revision does not match")
        if envelope.source_start_sequence_index <= prior_end:
            raise ValueError(
                "Session-map checkpoint evidence must be ordered and non-overlapping"
            )
        if envelope.source_end_sequence_index < envelope.source_start_sequence_index:
            raise ValueError("Session-map checkpoint evidence range is reversed")
        prior_end = envelope.source_end_sequence_index
