"""Minimal bounded schema for eviction-derived session state."""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

SESSION_MAP_ENTRY_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_SESSION_MAP_ENTRIES = 32
MAX_SESSION_MAP_ENTRY_TEXT_CHARS = 600
MAX_SESSION_MAP_TRAJECTORY_TEXT_CHARS = 4_000
MAX_SESSION_MAP_TEXT_CHARS = 16_000
MAX_SESSION_MAP_SOURCE_RANGES = 8


class SessionMapProvenanceError(ValueError):
    """An authored map cites evidence outside its available source scope."""


class SessionMapEntryKind(StrEnum):
    """Small set of current-state dimensions retained across eviction."""

    ORIENTATION = "orientation"
    GOAL = "goal"
    OPTION = "option"
    NEXT_ACTION = "next_action"
    DECISION = "decision"
    CONSTRAINT = "constraint"
    FINDING = "finding"
    OPEN_QUESTION = "open_question"
    ARTIFACT = "artifact"


class SessionMapEntryState(StrEnum):
    """Lifecycle state retained only when useful for current continuity."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CLOSED = "closed"


class SessionMapEntryBasis(StrEnum):
    """Whose evidence establishes the map entry's current meaning."""

    USER_ESTABLISHED = "user_established"
    ASSISTANT_PROPOSED = "assistant_proposed"
    TOOL_OBSERVED = "tool_observed"
    MIXED = "mixed"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SourceRange(_StrictFrozenModel):
    """Inclusive canonical raw-message range grounding one map claim."""

    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_order(self) -> SourceRange:
        if self.end < self.start:
            raise ValueError("source range end precedes start")
        return self


class SessionMapEntry(_StrictFrozenModel):
    """One concise, source-linked unit of current session state."""

    id: str = Field(pattern=SESSION_MAP_ENTRY_ID_PATTERN)
    kind: SessionMapEntryKind
    state: SessionMapEntryState
    basis: SessionMapEntryBasis
    text: str = Field(min_length=1, max_length=MAX_SESSION_MAP_ENTRY_TEXT_CHARS)
    sources: tuple[SourceRange, ...] = Field(
        min_length=1,
        max_length=MAX_SESSION_MAP_SOURCE_RANGES,
    )

    @model_validator(mode="after")
    def validate_source_order(self) -> SessionMapEntry:
        if self.id in {kind.value for kind in SessionMapEntryKind}:
            raise ValueError("entry ID must identify its semantic subject")
        _validate_ordered_source_ranges(self.sources, subject="entry")
        return self


class SessionMapTrajectory(_StrictFrozenModel):
    """Bounded source-linked narrative connecting the session's current state."""

    text: str = Field(min_length=1, max_length=MAX_SESSION_MAP_TRAJECTORY_TEXT_CHARS)
    sources: tuple[SourceRange, ...] = Field(
        min_length=1,
        max_length=MAX_SESSION_MAP_SOURCE_RANGES,
    )

    @model_validator(mode="after")
    def validate_source_order(self) -> SessionMapTrajectory:
        _validate_ordered_source_ranges(self.sources, subject="trajectory")
        return self


class SessionMapDraft(_StrictFrozenModel):
    """Complete bounded state authored from the prior map and new evidence."""

    schema_version: Literal[1, 2, 3] = 3
    trajectory: SessionMapTrajectory | None = None
    entries: tuple[SessionMapEntry, ...] = Field(
        default=(),
        max_length=MAX_SESSION_MAP_ENTRIES,
    )

    @model_validator(mode="after")
    def validate_map_bounds(self) -> SessionMapDraft:
        if self.schema_version == 1 and self.trajectory is not None:
            raise ValueError("session-map schema version 1 cannot contain a trajectory")
        if self.schema_version >= 2 and self.entries and self.trajectory is None:
            raise ValueError(
                f"session-map schema version {self.schema_version} requires a trajectory"
            )
        entry_ids = [entry.id for entry in self.entries]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("session-map entry IDs must be globally unique")
        orientations = [
            entry
            for entry in self.entries
            if entry.kind is SessionMapEntryKind.ORIENTATION
        ]
        if len(orientations) > 1:
            raise ValueError("session map may contain at most one orientation entry")
        if self.schema_version >= 3:
            _validate_entry_admission(self.entries)
        total_text_chars = sum(len(entry.text) for entry in self.entries) + (
            len(self.trajectory.text) if self.trajectory is not None else 0
        )
        if total_text_chars > MAX_SESSION_MAP_TEXT_CHARS:
            raise ValueError(
                f"session map text exceeds {MAX_SESSION_MAP_TEXT_CHARS} characters"
            )
        return self


_USER_COMMITMENT_KINDS = frozenset(
    {
        SessionMapEntryKind.GOAL,
        SessionMapEntryKind.NEXT_ACTION,
        SessionMapEntryKind.DECISION,
    }
)


def _validate_entry_admission(entries: Sequence[SessionMapEntry]) -> None:
    for entry in entries:
        if entry.kind in _USER_COMMITMENT_KINDS and entry.basis not in {
            SessionMapEntryBasis.USER_ESTABLISHED,
            SessionMapEntryBasis.MIXED,
        }:
            raise ValueError(
                f"{entry.kind.value} entry '{entry.id}' requires user-established "
                "or mixed evidence"
            )
        if (
            entry.kind
            in {
                SessionMapEntryKind.CONSTRAINT,
                SessionMapEntryKind.ARTIFACT,
            }
            and entry.basis is SessionMapEntryBasis.ASSISTANT_PROPOSED
        ):
            raise ValueError(
                f"{entry.kind.value} entry '{entry.id}' cannot be assistant-proposed"
            )


class _CanonicalEnvelope(Protocol):
    @property
    def source_start_sequence_index(self) -> int: ...

    @property
    def source_end_sequence_index(self) -> int: ...


def validate_session_map_provenance(
    draft: SessionMapDraft,
    *,
    envelopes: Sequence[_CanonicalEnvelope],
    previous_map: SessionMapDraft | None = None,
) -> SessionMapDraft:
    """Reject source ranges absent from the prior map and supplied evidence."""
    available = [
        SourceRange(
            start=envelope.source_start_sequence_index,
            end=envelope.source_end_sequence_index,
        )
        for envelope in envelopes
    ]
    if previous_map is not None:
        available.extend(
            source for entry in previous_map.entries for source in entry.sources
        )
        if previous_map.trajectory is not None:
            available.extend(previous_map.trajectory.sources)
    merged_available = _merge_source_ranges(available)
    if draft.trajectory is not None:
        for source in draft.trajectory.sources:
            if not _source_range_is_covered(source, merged_available):
                raise SessionMapProvenanceError(
                    "trajectory cites unavailable source range "
                    f"{source.start}-{source.end}"
                )
    for entry in draft.entries:
        for source in entry.sources:
            if not _source_range_is_covered(source, merged_available):
                raise SessionMapProvenanceError(
                    f"entry '{entry.id}' cites unavailable source range "
                    f"{source.start}-{source.end}"
                )
    return draft


def _validate_ordered_source_ranges(
    sources: Sequence[SourceRange], *, subject: str
) -> None:
    prior_end = -1
    for source in sources:
        if source.start <= prior_end:
            raise ValueError(
                f"{subject} source ranges must be ordered and non-overlapping"
            )
        prior_end = source.end


def _merge_source_ranges(ranges: Sequence[SourceRange]) -> tuple[SourceRange, ...]:
    merged: list[SourceRange] = []
    for source in sorted(ranges, key=lambda item: (item.start, item.end)):
        if not merged or source.start > merged[-1].end + 1:
            merged.append(source)
            continue
        prior = merged[-1]
        merged[-1] = SourceRange(start=prior.start, end=max(prior.end, source.end))
    return tuple(merged)


def _source_range_is_covered(
    source: SourceRange,
    available: Sequence[SourceRange],
) -> bool:
    return any(
        candidate.start <= source.start and candidate.end >= source.end
        for candidate in available
    )
