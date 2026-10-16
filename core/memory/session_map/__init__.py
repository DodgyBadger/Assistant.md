"""Bounded, source-linked session state derived from canonical chat evidence."""

from .authoring import build_session_map_authoring_prompt
from .evidence import (
    SessionMapEvidence,
    SessionMapEvidenceRangeResult,
    SessionMapMessageEvidence,
    build_session_map_evidence,
    resolve_session_map_evidence_range,
)
from .models import (
    MAX_SESSION_MAP_ENTRIES,
    MAX_SESSION_MAP_TRAJECTORY_TEXT_CHARS,
    SessionMapDraft,
    SessionMapEntry,
    SessionMapEntryBasis,
    SessionMapEntryKind,
    SessionMapEntryState,
    SessionMapProvenanceError,
    SessionMapTrajectory,
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
    "MAX_SESSION_MAP_TRAJECTORY_TEXT_CHARS",
    "SessionMapAuthoringRequest",
    "SessionMapAuthoringResult",
    "SessionMapDraft",
    "SessionMapEvidence",
    "SessionMapEvidenceRangeResult",
    "SessionMapEntry",
    "SessionMapEntryBasis",
    "SessionMapEntryKind",
    "SessionMapEntryState",
    "SessionMapMessageEvidence",
    "SessionMapProvenanceError",
    "SessionMapTrajectory",
    "SourceRange",
    "build_session_map_authoring_prompt",
    "build_session_map_evidence",
    "resolve_session_map_evidence_range",
    "run_session_map_authoring",
    "validate_session_map_provenance",
]
