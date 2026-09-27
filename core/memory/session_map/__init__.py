"""Bounded, source-linked session state derived from evicted chat evidence."""

from .authoring import SessionMapEvidenceEnvelope, build_session_map_authoring_prompt
from .models import (
    MAX_SESSION_MAP_ENTRIES,
    SessionMapDraft,
    SessionMapEntry,
    SessionMapEntryBasis,
    SessionMapEntryKind,
    SessionMapEntryState,
    SessionMapProvenanceError,
    SourceRange,
    validate_session_map_provenance,
)
from .service import (
    SessionMapAuthoringRequest,
    SessionMapAuthoringResult,
    run_session_map_authoring,
)

__all__ = [
    "MAX_SESSION_MAP_ENTRIES",
    "SessionMapAuthoringRequest",
    "SessionMapAuthoringResult",
    "SessionMapDraft",
    "SessionMapEntry",
    "SessionMapEntryBasis",
    "SessionMapEntryKind",
    "SessionMapEntryState",
    "SessionMapEvidenceEnvelope",
    "SessionMapProvenanceError",
    "SourceRange",
    "build_session_map_authoring_prompt",
    "run_session_map_authoring",
    "validate_session_map_provenance",
]
