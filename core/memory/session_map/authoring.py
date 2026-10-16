"""Prompt assembly for bounded source-linked session-map authoring."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from core.constants import (
    SESSION_MAP_AUTHORING_INSTRUCTION,
    SESSION_MAP_AUTHORING_PROMPT_VERSION,
)

from .evidence import SessionMapEvidence, SessionMapMessageEvidence
from .models import SessionMapDraft, SourceRange


def build_session_map_authoring_prompt(
    *,
    previous_map: SessionMapDraft,
    new_evidence: Sequence[SessionMapEvidence],
    recent_evidence: Sequence[SessionMapMessageEvidence] = (),
    retrieved_evidence: Sequence[SessionMapMessageEvidence] = (),
    retrieved_evidence_truncated: bool = False,
    focus: str | None = None,
    excluded_source_ranges: Sequence[SourceRange] = (),
) -> str:
    """Build one structured whole-map authoring request."""
    if not new_evidence:
        raise ValueError("Session-map authoring requires new canonical evidence")
    payload: dict[str, Any] = {
        "prompt_contract_version": SESSION_MAP_AUTHORING_PROMPT_VERSION,
        "instruction": SESSION_MAP_AUTHORING_INSTRUCTION,
        "user_focus": (focus or "").strip() or None,
        "previous_map": previous_map.model_dump(mode="json"),
        "new_evidence_envelopes": [
            {
                "envelope_id": evidence.evidence_id,
                "source_range": {
                    "start": evidence.source_start_sequence_index,
                    "end": evidence.source_end_sequence_index,
                },
                "citable_source_ranges": [
                    source.model_dump(mode="json")
                    for source in evidence.citable_source_ranges
                ],
                "projected_text": evidence.projected_text,
            }
            for evidence in new_evidence
        ],
        "retained_recent_evidence": [
            message.as_authoring_dict() for message in recent_evidence
        ],
        "retrieved_canonical_evidence": [
            message.as_authoring_dict() for message in retrieved_evidence
        ],
        "retrieved_evidence_truncated": retrieved_evidence_truncated,
        "excluded_source_ranges": [
            source.model_dump(mode="json") for source in excluded_source_ranges
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
