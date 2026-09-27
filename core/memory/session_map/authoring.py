"""Prompt assembly for bounded eviction-derived session-map authoring."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from core.constants import (
    SESSION_MAP_AUTHORING_INSTRUCTION,
    SESSION_MAP_AUTHORING_PROMPT_VERSION,
)

from .models import SessionMapDraft


class SessionMapEvidenceEnvelope(Protocol):
    """Structural evidence contract accepted by the map author."""

    @property
    def envelope_id(self) -> str: ...

    @property
    def session_id(self) -> str: ...

    @property
    def vault_name(self) -> str: ...

    @property
    def history_revision(self) -> int: ...

    @property
    def source_start_sequence_index(self) -> int: ...

    @property
    def source_end_sequence_index(self) -> int: ...

    @property
    def projected_text(self) -> str: ...


@dataclass(frozen=True)
class SessionMapRetainedMessage:
    """One newer canonical message available only as supersession lookahead."""

    sequence_index: int
    role: str
    content_text: str

    def __post_init__(self) -> None:
        if self.sequence_index < 0:
            raise ValueError("Retained lookahead sequence index cannot be negative")
        if not self.role.strip():
            raise ValueError("Retained lookahead requires a message role")


def build_session_map_authoring_prompt(
    *,
    previous_map: SessionMapDraft,
    envelopes: Sequence[SessionMapEvidenceEnvelope],
    retained_lookahead: Sequence[SessionMapRetainedMessage] = (),
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
        "retained_recent_lookahead": [
            {
                "sequence_index": message.sequence_index,
                "role": message.role,
                "content": message.content_text,
            }
            for message in retained_lookahead
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
