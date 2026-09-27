"""Bounded, source-linked session state derived from evicted chat evidence."""

from .authoring import SessionMapEvidenceEnvelope, build_session_map_authoring_prompt
from .checkpoints import (
    SESSION_MAP_CONTEXT_MARKER,
    SessionMapCheckpointResult,
    build_session_map_context_message,
    commit_session_map_checkpoint,
    load_session_map_checkpoint,
)
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
from .readiness import SessionMapReadiness, evaluate_session_map_readiness
from .service import (
    SessionMapAuthoringRequest,
    SessionMapAuthoringResult,
    run_session_map_authoring,
)

__all__ = [
    "MAX_SESSION_MAP_ENTRIES",
    "SESSION_MAP_CONTEXT_MARKER",
    "SessionMapAuthoringRequest",
    "SessionMapAuthoringResult",
    "SessionMapCheckpointResult",
    "SessionMapDraft",
    "SessionMapEntry",
    "SessionMapEntryBasis",
    "SessionMapEntryKind",
    "SessionMapEntryState",
    "SessionMapEvidenceEnvelope",
    "SessionMapProvenanceError",
    "SessionMapReadiness",
    "SourceRange",
    "build_session_map_authoring_prompt",
    "build_session_map_context_message",
    "commit_session_map_checkpoint",
    "evaluate_session_map_readiness",
    "load_session_map_checkpoint",
    "run_session_map_authoring",
    "validate_session_map_provenance",
]
