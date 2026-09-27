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

from .authoring import SessionMapEvidenceEnvelope
from .models import SessionMapDraft, validate_session_map_provenance

SESSION_MAP_CONTEXT_MARKER = "AssistantMD session map"


@dataclass(frozen=True)
class SessionMapCheckpointResult:
    """One committed map revision and its effective-history boundary."""

    checkpoint: StoredContextCheckpoint
    draft: SessionMapDraft


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
        envelopes=envelopes,
        previous_map=previous_map,
    )
    consumed_through = envelopes[-1].source_end_sequence_index
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
    if not checkpoint.metadata_json:
        raise ValueError("Session-map checkpoint is missing metadata")
    try:
        metadata = json.loads(checkpoint.metadata_json)
        map_payload = metadata["map"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("Session-map checkpoint metadata is invalid") from exc
    return SessionMapDraft.model_validate(map_payload)


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
