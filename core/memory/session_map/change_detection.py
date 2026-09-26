"""Typed decision signals for offline session-map change detection."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.llm.decision import DecisionRequest

SESSION_DELTA_PROMPT_CONTRACT_VERSION = "session-map-change-v1"
SESSION_RECONCILIATION_PROMPT_CONTRACT_VERSION = "session-map-reconcile-v2"
SESSION_CUMULATIVE_ADEQUACY_PROMPT_CONTRACT_VERSION = (
    "session-map-cumulative-adequacy-v3"
)
SESSION_MAP_DIMENSIONS = (
    "attention",
    "goals",
    "work_items",
    "decisions",
    "constraints",
    "commitments",
    "open_questions",
    "artifacts",
    "observations",
)
SESSION_MAP_ADEQUACY_FIELDS = tuple(
    f"{dimension}_adequate" for dimension in SESSION_MAP_DIMENSIONS
) + ("coverage_adequate",)

_SHARED_INSTRUCTIONS = """
Judge only durable session-state changes explicitly established by the supplied
canonical conversation delta. A change includes adding, revising, resolving,
rejecting, cancelling, superseding, or materially advancing the named state.
Do not treat a suggestion as adopted, a planned action as completed, file
creation as verification, or a failed/uncommitted attempt as canonical state.
Return independent raw yes-probabilities for every question.
""".strip()

_RECONCILIATION_INSTRUCTIONS = """
Judge whether the canonical conversation delta contains new durable evidence that
requires any reconciliation of the supplied current session map. Answer yes for
an addition, material revision, lifecycle change, resolution, supersession, or
foreground-attention change involving a goal, work item, decision, constraint,
commitment, open question, artifact, or material observation. Answer no when the
delta only acknowledges, restates, formats, or repeats state already represented
by the map, concerns transient conversation mechanics, or reports no new durable
state. Do not infer adoption, completion, or verification that the delta does not
establish. Return the raw yes-probability.
""".strip()

_CUMULATIVE_ADEQUACY_INSTRUCTIONS = """
Judge whether each field of the accepted current session map remains adequate in
light of every canonical message accumulated since that map was authored. A field
is inadequate when the cumulative delta establishes a durable addition, material
revision, lifecycle change, resolution, supersession, omission, or foreground
attention change that the accepted map does not represent. Minor changes may be
individually insufficient but material in combination. Treat tentative suggestions,
acknowledgements, repetitions, status queries, and conversation mechanics as stable
unless the cumulative evidence establishes changed durable state. Do not infer
adoption, completion, or verification. Return independent raw yes-probabilities:
yes means the named field remains adequate without generative reconciliation.
""".strip()


class SessionDeltaSignals(BaseModel):
    """Independent probabilities that a canonical delta dirtied map dimensions."""

    attention: float = Field(
        ge=0,
        le=1,
        description=(
            "Did the foreground active goal or active work focus materially change?"
        ),
    )
    goals: float = Field(
        ge=0,
        le=1,
        description=(
            "Did a desired outcome or its lifecycle state get added, changed, "
            "cancelled, completed, paused, or resumed?"
        ),
    )
    work_items: float = Field(
        ge=0,
        le=1,
        description=(
            "Did active work, progress, next action, ownership, or blocker state "
            "materially change?"
        ),
    )
    decisions: float = Field(
        ge=0,
        le=1,
        description=(
            "Did a governing choice become proposed, directed, accepted, rejected, "
            "superseded, or retired?"
        ),
    )
    constraints: float = Field(
        ge=0,
        le=1,
        description=(
            "Did a requirement, preference, limitation, scope boundary, or its "
            "applicability get added, revised, or waived?"
        ),
    )
    commitments: float = Field(
        ge=0,
        le=1,
        description=(
            "Did an actor make, fulfil, or cancel a concrete promise or obligation?"
        ),
    )
    open_questions: float = Field(
        ge=0,
        le=1,
        description=(
            "Was a material question opened, answered, reassigned, or withdrawn?"
        ),
    )
    artifacts: float = Field(
        ge=0,
        le=1,
        description=(
            "Did a work product or reference become proposed, created, changed, "
            "observed, verified, completed, or failed?"
        ),
    )
    observations: float = Field(
        ge=0,
        le=1,
        description=(
            "Did material working knowledge or its observed, assumed, or disputed "
            "status change?"
        ),
    )


class SessionReconciliationSignal(BaseModel):
    """Probability that a canonical delta requires any map reconciliation."""

    reconciliation_needed: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the delta contain new durable evidence requiring any change to "
            "the current session map or its foreground attention?"
        ),
    )


class SessionMapAdequacySignals(BaseModel):
    """Confidence that every map field remains adequate for cumulative evidence."""

    attention_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does foreground attention remain adequate without adding or changing "
            "the active goal or active work focus?"
        ),
    )
    goals_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the goals collection remain adequate without adding or changing "
            "a desired outcome or its lifecycle?"
        ),
    )
    work_items_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the work-items collection remain adequate without adding or "
            "changing active work, progress, ownership, blockers, or next actions?"
        ),
    )
    decisions_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the decisions collection remain adequate without adding or "
            "changing a proposed, adopted, rejected, superseded, or retired choice?"
        ),
    )
    constraints_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the constraints collection remain adequate without adding, "
            "revising, or waiving a material requirement or preference?"
        ),
    )
    commitments_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the commitments collection remain adequate without adding, "
            "fulfilling, or cancelling a concrete obligation?"
        ),
    )
    open_questions_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the open-questions collection remain adequate without opening, "
            "answering, reassigning, or withdrawing a material question?"
        ),
    )
    artifacts_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the artifacts collection remain adequate without adding or "
            "changing a work product, reference, or its verification state?"
        ),
    )
    observations_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Does the observations collection remain adequate without adding or "
            "changing material working knowledge or its epistemic state?"
        ),
    )
    coverage_adequate: float = Field(
        ge=0,
        le=1,
        description=(
            "Taken as a whole, does the map remain complete enough for current work "
            "without omitting any material new durable concept from the cumulative "
            "delta?"
        ),
    )


def build_session_delta_request(delta: str) -> DecisionRequest[SessionDeltaSignals]:
    """Build the provider-neutral request used by the labelled offline probe."""
    normalized = delta.strip()
    if not normalized:
        raise ValueError("session delta must not be empty")
    return DecisionRequest(
        state=normalized,
        output_type=SessionDeltaSignals,
        instructions=_SHARED_INSTRUCTIONS,
    )


def build_session_reconciliation_request(
    current_map: str, delta: str
) -> DecisionRequest[SessionReconciliationSignal]:
    """Build the v2 request that judges a delta relative to current map state."""
    normalized_map = current_map.strip()
    normalized_delta = delta.strip()
    if not normalized_map:
        raise ValueError("current session map must not be empty")
    if not normalized_delta:
        raise ValueError("session delta must not be empty")
    state = (
        "<current_session_map>\n"
        f"{normalized_map}\n"
        "</current_session_map>\n"
        "<canonical_delta>\n"
        f"{normalized_delta}\n"
        "</canonical_delta>"
    )
    return DecisionRequest(
        state=state,
        output_type=SessionReconciliationSignal,
        instructions=_RECONCILIATION_INSTRUCTIONS,
    )


def build_cumulative_session_map_adequacy_request(
    current_map: str, cumulative_delta: str
) -> DecisionRequest[SessionMapAdequacySignals]:
    """Judge one accepted map against its complete still-unmapped source range."""
    normalized_map = current_map.strip()
    normalized_delta = cumulative_delta.strip()
    if not normalized_map:
        raise ValueError("current session map must not be empty")
    if not normalized_delta:
        raise ValueError("cumulative session delta must not be empty")
    state = (
        "<accepted_session_map>\n"
        f"{normalized_map}\n"
        "</accepted_session_map>\n"
        "<cumulative_canonical_delta>\n"
        f"{normalized_delta}\n"
        "</cumulative_canonical_delta>"
    )
    return DecisionRequest(
        state=state,
        output_type=SessionMapAdequacySignals,
        instructions=_CUMULATIVE_ADEQUACY_INSTRUCTIONS,
    )
