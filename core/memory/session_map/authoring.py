"""Prompt assembly for bounded eviction-derived session-map authoring."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from core.constants import (
    SESSION_MAP_AUTHORING_INSTRUCTION,
    SESSION_MAP_AUTHORING_PROMPT_VERSION,
)

from .models import SessionMapDraft


class SessionMapEvidenceEnvelope(Protocol):
    """Structural evidence contract accepted by the map author."""

    envelope_id: str
    session_id: str
    vault_name: str
    history_revision: int
    source_start_sequence_index: int
    source_end_sequence_index: int
    projected_text: str


def build_session_map_authoring_prompt(
    *,
    previous_map: SessionMapDraft,
    envelopes: Sequence[SessionMapEvidenceEnvelope],
) -> str:
    """Build one structured whole-map authoring request."""
    if not envelopes:
        raise ValueError(
            "Session-map authoring requires at least one evidence envelope"
        )
    payload: dict[str, Any] = {
        "prompt_contract_version": SESSION_MAP_AUTHORING_PROMPT_VERSION,
        "instruction": SESSION_MAP_AUTHORING_INSTRUCTION,
        "previous_map": previous_map.model_dump(mode="json"),
        "new_evidence_envelopes": [
            {
                "envelope_id": envelope.envelope_id,
                "source_range": {
                    "start": envelope.source_start_sequence_index,
                    "end": envelope.source_end_sequence_index,
                },
                "projected_text": envelope.projected_text,
            }
            for envelope in envelopes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
