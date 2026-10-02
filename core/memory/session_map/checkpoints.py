"""Compaction context checkpoints backed by authored session maps."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelRequest, SystemPromptPart

from core.chat.chat_store import (
    ChatStore,
    ContextCheckpointKind,
    StoredContextCheckpoint,
)
from core.constants import (
    SESSION_MAP_CONTEXT_PREAMBLE,
    SESSION_MAP_CONTEXT_PROMPT_VERSION,
)
from core.llm.thinking import ThinkingValue

from .evidence import SessionMapEvidence, SessionMapMessageEvidence
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


def commit_session_map_context_checkpoint(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    draft: SessionMapDraft,
    previous_map: SessionMapDraft,
    new_evidence: tuple[SessionMapEvidence, ...],
    expected_history_revision: int,
    message_count_before: int,
    source: str,
    authoring_task_id: str | None = None,
    authoring_prompt_version: str | None = None,
    author_model_alias: str | None = None,
    author_thinking: ThinkingValue = None,
    checkpoint_id: str | None = None,
    recent_evidence: tuple[SessionMapMessageEvidence, ...] = (),
    retrieved_evidence: tuple[SessionMapMessageEvidence, ...] = (),
    map_observed_through_sequence_index: int | None = None,
    expected_previous_checkpoint_kind: ContextCheckpointKind | None = None,
) -> SessionMapCheckpointResult:
    """Atomically commit one map revision without changing canonical messages."""
    latest = store.get_latest_context_checkpoint(session_id, vault_name)
    if expected_previous_checkpoint_kind is not None:
        if (
            latest is None
            or latest.checkpoint_kind != expected_previous_checkpoint_kind
        ):
            raise ValueError(
                "Session-map checkpoint predecessor does not match: "
                f"expected {expected_previous_checkpoint_kind}"
            )
    elif latest is not None and latest.checkpoint_kind != "session_map":
        raise ValueError("Session-map checkpoints cannot follow recovery-card history")

    pending_evidence = None
    expected_evidence_start = 0
    if latest is not None and latest.checkpoint_kind == "session_map":
        pending_evidence = load_session_map_pending_evidence(latest)
        if pending_evidence is not None:
            if (
                pending_evidence.end_sequence_index
                != latest.last_message_sequence_index
            ):
                raise ValueError(
                    "Pending evidence must end at the prior eviction boundary"
                )
            expected_evidence_start = pending_evidence.start_sequence_index
        else:
            expected_evidence_start = latest.last_message_sequence_index + 1
    _validate_checkpoint_evidence(
        session_id=session_id,
        vault_name=vault_name,
        new_evidence=new_evidence,
        expected_history_revision=expected_history_revision,
        expected_source_start_sequence_index=expected_evidence_start,
    )
    validate_session_map_provenance(
        draft,
        evidence=(*new_evidence, *recent_evidence, *retrieved_evidence),
        previous_map=previous_map,
    )
    consumed_through = new_evidence[-1].source_end_sequence_index
    if latest is not None and latest.checkpoint_kind == "session_map":
        if consumed_through <= latest.last_message_sequence_index:
            raise ValueError("Session-map checkpoint boundary must advance")
    retained_prior = consumed_through
    for message in recent_evidence:
        if message.sequence_index <= retained_prior:
            raise ValueError("Retained evidence must follow the eviction boundary")
        retained_prior = message.sequence_index
    minimum_observed_through = max(
        [
            consumed_through,
            *(
                message.sequence_index
                for message in (*recent_evidence, *retrieved_evidence)
            ),
        ]
    )
    if latest is not None and latest.checkpoint_kind == "session_map":
        if pending_evidence is not None:
            # Retired gate checkpoints could evict evidence before rewriting the
            # map. Repair reauthors their complete pending interval; its new
            # cutoff must cover both that interval and any prior author exposure.
            previous_observed = _load_session_map_metadata(latest).get(
                "map_observed_through_sequence_index",
                latest.last_message_sequence_index,
            )
            if type(previous_observed) is not int or previous_observed < 0:
                raise ValueError("Session-map observed boundary metadata is invalid")
        else:
            previous_observed = latest.observed_through_sequence_index
        minimum_observed_through = max(minimum_observed_through, previous_observed)
    observed_through = (
        map_observed_through_sequence_index
        if map_observed_through_sequence_index is not None
        else minimum_observed_through
    )
    if type(observed_through) is not int or observed_through < minimum_observed_through:
        raise ValueError(
            "Session-map observed boundary must cover all authoring evidence"
        )
    if observed_through > store.get_highest_message_sequence_index(
        session_id, vault_name
    ):
        raise ValueError("Session-map observed boundary exceeds canonical history")

    resolved_checkpoint_id = checkpoint_id or uuid.uuid4().hex
    context_message = build_session_map_context_message(draft)
    metadata: dict[str, Any] = {
        "checkpoint_kind": "session_map",
        "prompt_contract_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
        "context_prompt_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
        "authoring_prompt_version": authoring_prompt_version,
        "map_schema_version": draft.schema_version,
        "author_model_alias": author_model_alias,
        "author_thinking": author_thinking,
        "source_history_revision": expected_history_revision,
        "consumed_through_sequence_index": consumed_through,
        "map_observed_through_sequence_index": observed_through,
        "evidence_source_start_sequence_index": (
            new_evidence[0].source_start_sequence_index
        ),
        "evidence_source_end_sequence_index": (
            new_evidence[-1].source_end_sequence_index
        ),
        "evidence_envelope_ids": [evidence.evidence_id for evidence in new_evidence],
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
        replacement_source_sequence_indexes=[None],
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
    if checkpoint.checkpoint_kind != "session_map":
        raise ValueError("Context checkpoint is not a session map")
    return checkpoint.observed_through_sequence_index


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
    new_evidence: tuple[SessionMapEvidence, ...],
    expected_history_revision: int,
    expected_source_start_sequence_index: int,
) -> None:
    if not new_evidence:
        raise ValueError("Session-map checkpoint requires evidence envelopes")
    prior_end = expected_source_start_sequence_index - 1
    for evidence in new_evidence:
        if evidence.session_id != session_id or evidence.vault_name != vault_name:
            raise ValueError("Session-map checkpoint evidence scope does not match")
        if evidence.history_revision != expected_history_revision:
            raise ValueError("Session-map checkpoint evidence revision does not match")
        if evidence.source_start_sequence_index != prior_end + 1:
            raise ValueError(
                "Session-map checkpoint evidence must cover a contiguous prefix"
            )
        if evidence.source_end_sequence_index < evidence.source_start_sequence_index:
            raise ValueError("Session-map checkpoint evidence range is reversed")
        if evidence.message_count != (
            evidence.source_end_sequence_index
            - evidence.source_start_sequence_index
            + 1
        ):
            raise ValueError(
                "Session-map checkpoint evidence count does not match its range"
            )
        prior_end = evidence.source_end_sequence_index
