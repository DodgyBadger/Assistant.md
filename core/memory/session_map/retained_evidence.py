"""Canonical retained history and verified transcript-window evidence for maps."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence

from pydantic_ai.messages import ModelMessage

from core.chat.chat_store import ChatStore, StoredChatMessage

from .evidence import (
    SessionMapMessageEvidence,
    SessionMapRetrievedEvidence,
    bound_retrieved_session_map_evidence,
    contains_session_map_retrieval_result,
    has_contiguous_canonical_sequences,
    is_session_map_retrieval_part,
    project_session_map_message,
)


def project_retained_session_map_evidence(
    retained: Sequence[StoredChatMessage],
) -> tuple[SessionMapMessageEvidence, ...]:
    """Cite the canonical retained suffix without raw retrieval tool returns."""
    if not retained or not has_contiguous_canonical_sequences(retained):
        raise ValueError("Retained session-map evidence is not canonical")
    projected: list[SessionMapMessageEvidence] = []
    for stored in retained:
        projection = project_session_map_message(stored.message)
        if projection is None:
            continue
        projected.append(
            SessionMapMessageEvidence(
                sequence_index=stored.sequence_index,
                role=projection.role,
                content_text=projection.content_text,
            )
        )
    return tuple(projected)


def project_retrieved_session_map_evidence(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    retained: Sequence[StoredChatMessage],
) -> SessionMapRetrievedEvidence:
    """Admit bounded transcript fragments after verifying selected canonical rows."""
    source_boundaries = _session_map_retrieval_source_boundaries(
        store=store, session_id=session_id, vault_name=vault_name
    )
    canonical_messages: dict[int, StoredChatMessage | None] = {}

    def verified_fragments() -> Iterator[SessionMapMessageEvidence]:
        for stored in retained:
            yield from _session_ops_window_fragments(
                stored.message,
                store=store,
                session_id=session_id,
                vault_name=vault_name,
                source_boundaries=source_boundaries,
                canonical_messages=canonical_messages,
            )

    return bound_retrieved_session_map_evidence(verified_fragments())


def _session_map_retrieval_source_boundaries(
    *, store: ChatStore, session_id: str, vault_name: str
) -> dict[str, int | None]:
    """Resolve ancestor identities only within their inherited canonical prefixes."""
    boundaries: dict[str, int | None] = {session_id: None}
    current_session = session_id
    inherited_through: int | None = None
    while True:
        lineage = store.get_session_metadata(current_session, vault_name).get("fork")
        if not isinstance(lineage, dict):
            break
        source = lineage.get("source_session_id")
        through = lineage.get("through_sequence_index")
        if (
            not isinstance(source, str)
            or not source
            or source in boundaries
            or not isinstance(through, int)
            or isinstance(through, bool)
            or through < 0
        ):
            break
        inherited_through = (
            through if inherited_through is None else min(inherited_through, through)
        )
        boundaries[source] = inherited_through
        current_session = source
    return boundaries


def _session_ops_window_fragments(
    message: ModelMessage,
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    source_boundaries: dict[str, int | None],
    canonical_messages: dict[int, StoredChatMessage | None],
) -> Iterator[SessionMapMessageEvidence]:
    """Verify exact window fragments against selected child-owned canonical rows."""
    for part in message.parts:
        if not is_session_map_retrieval_part(part):
            continue
        payload = part.content
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        if payload.get("operation") != "get_transcript_window":
            continue
        source_session = payload.get("session_id")
        if (
            payload.get("status") != "ok"
            or not isinstance(source_session, str)
            or source_session not in source_boundaries
        ):
            continue
        payload_vault = payload.get("vault_name")
        if payload_vault is not None and payload_vault != vault_name:
            continue
        messages = payload.get("messages")
        if not isinstance(messages, list):
            continue
        for item in messages:
            if not isinstance(item, dict):
                continue
            sequence_index = item.get("sequence_index")
            source_boundary = source_boundaries[source_session]
            if (
                not isinstance(sequence_index, int)
                or isinstance(sequence_index, bool)
                or sequence_index < 0
                or (source_boundary is not None and sequence_index > source_boundary)
            ):
                continue
            content = item.get("content")
            start = item.get("content_start", 0)
            end = item.get(
                "content_end", len(content) if isinstance(content, str) else 0
            )
            complete = item.get("content_complete", True)
            if (
                not isinstance(content, str)
                or not content
                or not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or not isinstance(complete, bool)
                or start < 0
                or end - start != len(content)
            ):
                continue
            if sequence_index not in canonical_messages:
                selected = store.get_stored_messages_range(
                    session_id,
                    vault_name,
                    after_sequence_index=sequence_index - 1,
                    through_sequence_index=sequence_index,
                )
                canonical_messages[sequence_index] = (
                    selected[0] if len(selected) == 1 else None
                )
            canonical = canonical_messages[sequence_index]
            if canonical is None:
                continue
            if (
                item.get("role") != canonical.role
                or end > len(canonical.content_text)
                or canonical.content_text[start:end] != content
                or complete != (start == 0 and end == len(canonical.content_text))
                or contains_session_map_retrieval_result(canonical.message)
            ):
                continue
            yield SessionMapMessageEvidence(
                sequence_index=sequence_index,
                role=canonical.role,
                content_text=content,
                content_start=start,
                content_end=end,
                content_complete=complete,
            )
