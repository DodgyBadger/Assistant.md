"""Deterministic domain model for source-linked live session memory."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

ENTRY_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"
MAX_ENTRIES_PER_KIND = 32
MAX_TOTAL_ENTRIES = 128
MAX_PATCH_OPERATIONS = 64


class SessionMapError(ValueError):
    """Base error for deterministic session-map operations."""


class SessionMapConflictError(SessionMapError):
    """A patch was authored against a different map revision."""


class SessionMapRenderError(SessionMapError):
    """The required current handoff cannot fit in the render budget."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceRef(_StrictModel):
    """Role-bearing reference to one canonical raw chat message."""

    sequence_index: int = Field(ge=0)
    role: Literal["user", "assistant", "tool"]


class EffectiveTime(_StrictModel):
    """Optional real-world validity interval distinct from revision time."""

    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> EffectiveTime:
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValueError("effective-time end precedes start")
        return self


class GoalStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class WorkItemStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DecisionStatus(StrEnum):
    ACTIVE = "active"
    REJECTED = "rejected"
    RETIRED = "retired"


class AdoptionStatus(StrEnum):
    PROPOSED = "proposed"
    USER_DIRECTED = "user_directed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ConstraintStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class QuestionStatus(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"
    WITHDRAWN = "withdrawn"


class ArtifactStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    COMPLETE = "complete"
    FAILED = "failed"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    OBSERVED = "observed"
    VERIFIED = "verified"
    FAILED = "failed"


class EpistemicStatus(StrEnum):
    OBSERVED = "observed"
    ASSUMED = "assumed"
    DISPUTED = "disputed"


class Relevance(StrEnum):
    REQUIRED_FOR_ACTIVE_WORK = "required_for_active_work"
    SUPPORTING = "supporting"
    BACKGROUND = "background"


class EntryBase(_StrictModel):
    """Fields shared by every current working-set entry."""

    id: str = Field(pattern=ENTRY_ID_PATTERN)
    source_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=16)
    state_source_refs: tuple[SourceRef, ...] = Field(default=(), max_length=16)
    active_from_sequence_index: int | None = Field(default=None, ge=0)
    active_until_sequence_index: int | None = Field(default=None, ge=0)
    last_state_change_sequence_index: int | None = Field(default=None, ge=0)
    effective_time: EffectiveTime | None = None

    @model_validator(mode="after")
    def validate_active_interval(self) -> EntryBase:
        if (
            self.active_from_sequence_index is not None
            and self.active_until_sequence_index is not None
            and self.active_until_sequence_index < self.active_from_sequence_index
        ):
            raise ValueError("active interval ends before it starts")
        return self


class GoalEntry(EntryBase):
    kind: Literal["goal"] = "goal"
    text: str = Field(min_length=1, max_length=1000)
    status: GoalStatus


class WorkItemEntry(EntryBase):
    kind: Literal["work_item"] = "work_item"
    goal_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    text: str = Field(min_length=1, max_length=1000)
    status: WorkItemStatus
    next_action: str | None = Field(default=None, max_length=1000)
    next_action_owner: Literal["user", "assistant", "external"] | None = None
    blocker_ids: tuple[str, ...] = Field(default=(), max_length=16)


class DecisionEntry(EntryBase):
    kind: Literal["decision"] = "decision"
    text: str = Field(min_length=1, max_length=1000)
    status: DecisionStatus
    adoption_status: AdoptionStatus
    scope: str = Field(min_length=1, max_length=256)


class ConstraintEntry(EntryBase):
    kind: Literal["constraint"] = "constraint"
    text: str = Field(min_length=1, max_length=1000)
    status: ConstraintStatus
    scope: str = Field(min_length=1, max_length=256)


class CommitmentEntry(EntryBase):
    kind: Literal["commitment"] = "commitment"
    actor: Literal["user", "assistant", "external"]
    text: str = Field(min_length=1, max_length=1000)
    status: CommitmentStatus


class OpenQuestionEntry(EntryBase):
    kind: Literal["open_question"] = "open_question"
    text: str = Field(min_length=1, max_length=1000)
    status: QuestionStatus
    owner: Literal["user", "assistant", "external"] | None = None
    answer_ref: str | None = Field(default=None, max_length=512)


class ArtifactEntry(EntryBase):
    kind: Literal["artifact"] = "artifact"
    ref: str = Field(min_length=1, max_length=1000)
    artifact_kind: str = Field(min_length=1, max_length=128)
    status: ArtifactStatus
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    status_detail: str | None = Field(default=None, max_length=1000)


class ObservationEntry(EntryBase):
    kind: Literal["observation"] = "observation"
    text: str = Field(min_length=1, max_length=1000)
    epistemic_status: EpistemicStatus
    relevance: Relevance


SessionMapEntry = Annotated[
    GoalEntry
    | WorkItemEntry
    | DecisionEntry
    | ConstraintEntry
    | CommitmentEntry
    | OpenQuestionEntry
    | ArtifactEntry
    | ObservationEntry,
    Field(discriminator="kind"),
]


class Attention(_StrictModel):
    active_goal_ids: tuple[str, ...] = Field(default=(), max_length=8)
    active_work_item_id: str | None = Field(default=None, pattern=ENTRY_ID_PATTERN)
    changed_at_sequence_index: int | None = Field(default=None, ge=0)


class SessionMap(_StrictModel):
    """One immutable current working-set revision for a chat session."""

    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1, max_length=256)
    revision: int = Field(ge=0)
    updated_through_sequence_index: int = Field(ge=-1)
    observed_source_content_revision: int = Field(ge=0)
    created_at: datetime
    updated_at: datetime
    authoring_status: Literal["empty", "complete"] = "complete"
    prompt_contract_version: str | None = Field(default=None, max_length=128)
    decision_model: str | None = Field(default=None, max_length=256)
    authoring_model: str | None = Field(default=None, max_length=256)
    error: dict[str, JsonValue] | None = None
    attention: Attention = Field(default_factory=Attention)
    goals: tuple[GoalEntry, ...] = Field(default=(), max_length=MAX_ENTRIES_PER_KIND)
    work_items: tuple[WorkItemEntry, ...] = Field(
        default=(), max_length=MAX_ENTRIES_PER_KIND
    )
    decisions: tuple[DecisionEntry, ...] = Field(
        default=(), max_length=MAX_ENTRIES_PER_KIND
    )
    constraints: tuple[ConstraintEntry, ...] = Field(
        default=(), max_length=MAX_ENTRIES_PER_KIND
    )
    commitments: tuple[CommitmentEntry, ...] = Field(
        default=(), max_length=MAX_ENTRIES_PER_KIND
    )
    open_questions: tuple[OpenQuestionEntry, ...] = Field(
        default=(), max_length=MAX_ENTRIES_PER_KIND
    )
    artifacts: tuple[ArtifactEntry, ...] = Field(
        default=(), max_length=MAX_ENTRIES_PER_KIND
    )
    observations: tuple[ObservationEntry, ...] = Field(
        default=(), max_length=MAX_ENTRIES_PER_KIND
    )

    @classmethod
    def empty(cls, *, session_id: str, created_at: datetime) -> SessionMap:
        """Construct the deterministic empty predecessor revision."""
        return cls(
            session_id=session_id,
            revision=0,
            updated_through_sequence_index=-1,
            observed_source_content_revision=0,
            created_at=created_at,
            updated_at=created_at,
            authoring_status="empty",
        )

    @model_validator(mode="after")
    def validate_map(self) -> SessionMap:
        entries = _all_entries(self)
        if len(entries) > MAX_TOTAL_ENTRIES:
            raise ValueError(f"session map exceeds {MAX_TOTAL_ENTRIES} entries")
        ids = [entry.id for entry in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("session-map entry IDs must be globally unique")
        by_id = {entry.id: entry for entry in entries}
        for entry in entries:
            for ref in (*entry.source_refs, *entry.state_source_refs):
                if ref.sequence_index > self.updated_through_sequence_index:
                    raise ValueError(
                        f"entry '{entry.id}' cites a source beyond map coverage"
                    )
            for timeline_index in (
                entry.active_from_sequence_index,
                entry.active_until_sequence_index,
                entry.last_state_change_sequence_index,
            ):
                if (
                    timeline_index is not None
                    and timeline_index > self.updated_through_sequence_index
                ):
                    raise ValueError(
                        f"entry '{entry.id}' state lies beyond map coverage"
                    )
        for goal_id in self.attention.active_goal_ids:
            goal = by_id.get(goal_id)
            if not isinstance(goal, GoalEntry) or goal.status != GoalStatus.ACTIVE:
                raise ValueError(
                    "attention.active_goal_ids must reference active goals"
                )
        work_id = self.attention.active_work_item_id
        if work_id is not None:
            work = by_id.get(work_id)
            if not isinstance(work, WorkItemEntry) or work.status not in {
                WorkItemStatus.IN_PROGRESS,
                WorkItemStatus.BLOCKED,
            }:
                raise ValueError(
                    "attention.active_work_item_id must reference active work"
                )
        goal_ids = {goal.id for goal in self.goals}
        for work in self.work_items:
            if not set(work.goal_ids) <= goal_ids:
                raise ValueError(f"work item '{work.id}' references an unknown goal")
        if (
            self.attention.changed_at_sequence_index is not None
            and self.attention.changed_at_sequence_index
            > self.updated_through_sequence_index
        ):
            raise ValueError("attention change lies beyond map coverage")
        return self


class AddPatch(_StrictModel):
    operation: Literal["add"] = "add"
    entry: SessionMapEntry


class UpdatePatch(_StrictModel):
    operation: Literal["update"] = "update"
    entry_id: str = Field(pattern=ENTRY_ID_PATTERN)
    changes: dict[str, JsonValue] = Field(min_length=1, max_length=8)
    evidence_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=16)


class SupersedePatch(_StrictModel):
    operation: Literal["supersede"] = "supersede"
    entry_id: str = Field(pattern=ENTRY_ID_PATTERN)
    replacement: SessionMapEntry
    evidence_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=16)


class ResolvePatch(_StrictModel):
    operation: Literal["resolve"] = "resolve"
    entry_id: str = Field(pattern=ENTRY_ID_PATTERN)
    terminal_status: str = Field(min_length=1, max_length=64)
    evidence_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=16)


class ChangeAttentionPatch(_StrictModel):
    operation: Literal["change_attention"] = "change_attention"
    active_goal_ids: tuple[str, ...] = Field(default=(), max_length=8)
    active_work_item_id: str | None = Field(default=None, pattern=ENTRY_ID_PATTERN)
    evidence_refs: tuple[SourceRef, ...] = Field(min_length=1, max_length=16)


class NoopPatch(_StrictModel):
    operation: Literal["noop"] = "noop"
    reason: str | None = Field(default=None, max_length=256)


PatchOperation = Annotated[
    AddPatch
    | UpdatePatch
    | SupersedePatch
    | ResolvePatch
    | ChangeAttentionPatch
    | NoopPatch,
    Field(discriminator="operation"),
]


class MapPatchSet(_StrictModel):
    expected_revision: int = Field(ge=0)
    through_sequence_index: int = Field(ge=0)
    observed_source_content_revision: int = Field(ge=1)
    operations: tuple[PatchOperation, ...] = Field(
        min_length=1, max_length=MAX_PATCH_OPERATIONS
    )

    @model_validator(mode="after")
    def validate_noop(self) -> MapPatchSet:
        if (
            any(isinstance(item, NoopPatch) for item in self.operations)
            and len(self.operations) != 1
        ):
            raise ValueError("noop must be the only patch operation")
        return self


class RenderedSessionMap(_StrictModel):
    text: str
    included_entry_ids: tuple[str, ...]
    omitted_entry_ids: tuple[str, ...]


_COLLECTION_BY_KIND = {
    "goal": "goals",
    "work_item": "work_items",
    "decision": "decisions",
    "constraint": "constraints",
    "commitment": "commitments",
    "open_question": "open_questions",
    "artifact": "artifacts",
    "observation": "observations",
}

_MUTABLE_FIELDS: dict[str, frozenset[str]] = {
    "goal": frozenset({"status"}),
    "work_item": frozenset(
        {"status", "next_action", "next_action_owner", "blocker_ids"}
    ),
    "decision": frozenset({"status", "adoption_status"}),
    "constraint": frozenset({"status", "active_until_sequence_index"}),
    "commitment": frozenset({"status"}),
    "open_question": frozenset({"status", "owner", "answer_ref"}),
    "artifact": frozenset({"status", "verification_status", "status_detail"}),
    "observation": frozenset({"epistemic_status", "relevance"}),
}

_TERMINAL_STATUSES: dict[str, frozenset[str]] = {
    "goal": frozenset({"completed", "cancelled"}),
    "work_item": frozenset({"completed", "cancelled"}),
    "decision": frozenset({"rejected", "retired"}),
    "constraint": frozenset({"inactive"}),
    "commitment": frozenset({"fulfilled", "cancelled"}),
    "open_question": frozenset({"answered", "withdrawn"}),
    "artifact": frozenset({"complete", "failed"}),
    "observation": frozenset(),
}

_ALLOWED_TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {
    "goal.status": {
        "proposed": frozenset({"active", "cancelled"}),
        "active": frozenset({"paused", "completed", "cancelled"}),
        "paused": frozenset({"active", "cancelled"}),
    },
    "work_item.status": {
        "planned": frozenset({"in_progress", "completed", "cancelled"}),
        "in_progress": frozenset({"blocked", "completed", "cancelled"}),
        "blocked": frozenset({"in_progress", "completed", "cancelled"}),
    },
    "decision.status": {"active": frozenset({"rejected", "retired"})},
    "decision.adoption_status": {
        "proposed": frozenset({"user_directed", "accepted", "rejected"}),
        "user_directed": frozenset({"accepted", "rejected"}),
        "accepted": frozenset({"rejected"}),
    },
    "constraint.status": {
        "active": frozenset({"inactive"}),
        "inactive": frozenset({"active"}),
    },
    "commitment.status": {"open": frozenset({"fulfilled", "cancelled"})},
    "open_question.status": {"open": frozenset({"answered", "withdrawn"})},
    "artifact.status": {
        "proposed": frozenset({"active", "failed"}),
        "active": frozenset({"complete", "failed"}),
        "failed": frozenset({"active"}),
    },
    "artifact.verification_status": {
        "unverified": frozenset({"observed", "verified", "failed"}),
        "observed": frozenset({"verified", "failed"}),
        "verified": frozenset({"failed"}),
        "failed": frozenset({"observed", "verified"}),
    },
    "observation.epistemic_status": {
        "assumed": frozenset({"observed", "disputed"}),
        "observed": frozenset({"disputed"}),
        "disputed": frozenset({"assumed", "observed"}),
    },
}


def apply_patch_set(
    current: SessionMap,
    patch_set: MapPatchSet,
    *,
    created_at: datetime,
) -> SessionMap:
    """Apply a validated patch set without regenerating unchanged entries."""
    if patch_set.expected_revision != current.revision:
        raise SessionMapConflictError(
            f"expected revision {patch_set.expected_revision}, found {current.revision}"
        )
    if patch_set.through_sequence_index <= current.updated_through_sequence_index:
        raise SessionMapError("patch coverage must advance beyond the current map")
    if (
        patch_set.observed_source_content_revision
        <= current.observed_source_content_revision
    ):
        raise SessionMapError("source-content revision must advance")

    values = current.model_dump(mode="python")
    entries = {entry.id: entry for entry in _all_entries(current)}
    for operation in patch_set.operations:
        if isinstance(operation, NoopPatch):
            continue
        if isinstance(operation, AddPatch):
            _validate_new_entry(operation.entry, entries, current, patch_set)
            entries[operation.entry.id] = operation.entry
            continue
        if isinstance(operation, ChangeAttentionPatch):
            _validate_delta_refs(operation.evidence_refs, current, patch_set)
            values["attention"] = Attention(
                active_goal_ids=operation.active_goal_ids,
                active_work_item_id=operation.active_work_item_id,
                changed_at_sequence_index=max(
                    ref.sequence_index for ref in operation.evidence_refs
                ),
            )
            continue

        existing = entries.get(operation.entry_id)
        if existing is None:
            raise SessionMapError(f"unknown entry ID '{operation.entry_id}'")
        _validate_delta_refs(operation.evidence_refs, current, patch_set)
        if isinstance(operation, SupersedePatch):
            if operation.replacement.kind != existing.kind:
                raise SessionMapError("supersession must preserve entry kind")
            _validate_new_entry(operation.replacement, entries, current, patch_set)
            del entries[existing.id]
            entries[operation.replacement.id] = operation.replacement
        elif isinstance(operation, UpdatePatch):
            entries[existing.id] = _updated_entry(
                existing, operation.changes, operation.evidence_refs
            )
        elif isinstance(operation, ResolvePatch):
            if operation.terminal_status not in _TERMINAL_STATUSES[existing.kind]:
                raise SessionMapError(
                    f"'{operation.terminal_status}' is not terminal for {existing.kind}"
                )
            entries[existing.id] = _updated_entry(
                existing,
                {"status": operation.terminal_status},
                operation.evidence_refs,
            )

    for kind, collection in _COLLECTION_BY_KIND.items():
        values[collection] = tuple(
            entry for entry in entries.values() if entry.kind == kind
        )
    values.update(
        revision=current.revision + 1,
        updated_through_sequence_index=patch_set.through_sequence_index,
        observed_source_content_revision=patch_set.observed_source_content_revision,
        updated_at=created_at,
        authoring_status="complete",
        error=None,
    )
    return SessionMap.model_validate(values)


def render_session_map(
    session_map: SessionMap, *, max_chars: int
) -> RenderedSessionMap:
    """Render one bounded working set using an explicit stable priority order."""
    if max_chars < 128:
        raise SessionMapRenderError("session-map render budget must be at least 128")
    by_id = {entry.id: entry for entry in _all_entries(session_map)}
    required_id_candidates = list(session_map.attention.active_goal_ids)
    if session_map.attention.active_work_item_id is not None:
        required_id_candidates.append(session_map.attention.active_work_item_id)
    required_ids = tuple(dict.fromkeys(required_id_candidates))
    ordered = [by_id[item] for item in required_ids]
    remainder = [
        entry for entry in _all_entries(session_map) if entry.id not in required_ids
    ]
    remainder.sort(key=_render_priority)
    ordered.extend(remainder)

    lines = [
        f"Session map revision {session_map.revision}",
        f"Covered through canonical message {session_map.updated_through_sequence_index}",
    ]
    included: list[str] = []
    omitted: list[str] = []
    for entry in ordered:
        line = _render_entry(entry)
        candidate = "\n".join((*lines, line))
        if len(candidate) <= max_chars:
            lines.append(line)
            included.append(entry.id)
        elif entry.id in required_ids:
            raise SessionMapRenderError(
                f"required current entry '{entry.id}' exceeds render budget"
            )
        else:
            omitted.append(entry.id)
    return RenderedSessionMap(
        text="\n".join(lines),
        included_entry_ids=tuple(included),
        omitted_entry_ids=tuple(omitted),
    )


def session_map_entries(session_map: SessionMap) -> tuple[SessionMapEntry, ...]:
    """Return all current entries in stable collection order."""
    return _all_entries(session_map)


def patch_source_refs(operation: PatchOperation) -> tuple[SourceRef, ...]:
    """Return every canonical evidence reference carried by one patch."""
    if isinstance(operation, AddPatch):
        return (*operation.entry.source_refs, *operation.entry.state_source_refs)
    if isinstance(operation, SupersedePatch):
        return (
            *operation.evidence_refs,
            *operation.replacement.source_refs,
            *operation.replacement.state_source_refs,
        )
    if isinstance(operation, UpdatePatch | ResolvePatch | ChangeAttentionPatch):
        return operation.evidence_refs
    return ()


def _all_entries(session_map: SessionMap) -> tuple[SessionMapEntry, ...]:
    return (
        *session_map.goals,
        *session_map.work_items,
        *session_map.decisions,
        *session_map.constraints,
        *session_map.commitments,
        *session_map.open_questions,
        *session_map.artifacts,
        *session_map.observations,
    )


def _validate_delta_refs(
    refs: tuple[SourceRef, ...], current: SessionMap, patch_set: MapPatchSet
) -> None:
    for ref in refs:
        if not (
            current.updated_through_sequence_index
            < ref.sequence_index
            <= patch_set.through_sequence_index
        ):
            raise SessionMapError(
                f"source reference {ref.sequence_index} lies outside patch delta"
            )


def _validate_new_entry(
    entry: SessionMapEntry,
    entries: dict[str, SessionMapEntry],
    current: SessionMap,
    patch_set: MapPatchSet,
) -> None:
    if entry.id in entries:
        raise SessionMapError(f"entry ID '{entry.id}' already exists")
    _validate_delta_refs(entry.source_refs, current, patch_set)
    _validate_delta_refs(entry.state_source_refs, current, patch_set)


def _updated_entry(
    entry: SessionMapEntry,
    changes: dict[str, JsonValue],
    evidence_refs: tuple[SourceRef, ...],
) -> SessionMapEntry:
    allowed = _MUTABLE_FIELDS[entry.kind]
    for field_name in changes:
        if field_name not in allowed:
            raise SessionMapError(
                f"identity-bearing field '{field_name}' requires supersession"
            )
        _validate_transition(entry, field_name, changes[field_name])
    values = entry.model_dump(mode="python")
    values.update(changes)
    values["state_source_refs"] = _merge_source_refs(
        entry.state_source_refs, evidence_refs
    )
    values["last_state_change_sequence_index"] = max(
        ref.sequence_index for ref in evidence_refs
    )
    return type(entry).model_validate(values)


def _merge_source_refs(
    existing: tuple[SourceRef, ...], added: tuple[SourceRef, ...]
) -> tuple[SourceRef, ...]:
    unique: dict[tuple[int, str], SourceRef] = {}
    for ref in (*existing, *added):
        unique.setdefault((ref.sequence_index, ref.role), ref)
    return tuple(unique.values())[-16:]


def _validate_transition(
    entry: SessionMapEntry, field_name: str, raw_new_value: JsonValue
) -> None:
    transition_key = f"{entry.kind}.{field_name}"
    transitions = _ALLOWED_TRANSITIONS.get(transition_key)
    if transitions is None:
        return
    old_value = str(getattr(entry, field_name))
    new_value = str(raw_new_value)
    if old_value == new_value:
        raise SessionMapError(
            f"entry '{entry.id}' {field_name} update does not change state"
        )
    if new_value not in transitions.get(old_value, frozenset()):
        raise SessionMapError(
            f"illegal {transition_key} transition from '{old_value}' to '{new_value}'"
        )


def _render_priority(entry: SessionMapEntry) -> tuple[int, int, int, str]:
    kind_priority = {
        "open_question": 0,
        "constraint": 1,
        "decision": 2,
        "commitment": 3,
        "artifact": 4,
        "observation": 5,
        "work_item": 6,
        "goal": 7,
    }
    transition = entry.last_state_change_sequence_index or -1
    return (
        0 if _entry_is_current(entry) else 1,
        kind_priority[entry.kind],
        -transition,
        entry.id,
    )


def _render_entry(entry: SessionMapEntry) -> str:
    if isinstance(entry, ArtifactEntry):
        state = f"{entry.status}, {entry.verification_status}"
        body = entry.ref
    elif isinstance(entry, DecisionEntry):
        state = f"{entry.status}, {entry.adoption_status}"
        body = f"{entry.text} Scope: {entry.scope}."
    elif isinstance(entry, ConstraintEntry):
        state = str(entry.status)
        body = f"{entry.text} Scope: {entry.scope}."
    elif isinstance(entry, ObservationEntry):
        state = f"{entry.epistemic_status}, {entry.relevance}"
        body = entry.text
    else:
        state = str(entry.status)
        body = entry.text
    if isinstance(entry, WorkItemEntry) and entry.next_action:
        body = f"{body} Next: {entry.next_action}"
    if isinstance(entry, WorkItemEntry) and entry.blocker_ids:
        body = f"{body} Blocked by: {', '.join(entry.blocker_ids)}."
    return f"- [{entry.kind}:{entry.id}; {state}] {body}"


def _entry_is_current(entry: SessionMapEntry) -> bool:
    if isinstance(entry, GoalEntry):
        return entry.status in {GoalStatus.ACTIVE, GoalStatus.PAUSED}
    if isinstance(entry, WorkItemEntry):
        return entry.status in {WorkItemStatus.IN_PROGRESS, WorkItemStatus.BLOCKED}
    if isinstance(entry, DecisionEntry):
        return entry.status == DecisionStatus.ACTIVE
    if isinstance(entry, ConstraintEntry):
        return entry.status == ConstraintStatus.ACTIVE
    if isinstance(entry, CommitmentEntry):
        return entry.status == CommitmentStatus.OPEN
    if isinstance(entry, OpenQuestionEntry):
        return entry.status == QuestionStatus.OPEN
    if isinstance(entry, ArtifactEntry):
        return entry.status in {ArtifactStatus.PROPOSED, ArtifactStatus.ACTIVE}
    return entry.relevance == Relevance.REQUIRED_FOR_ACTIVE_WORK
