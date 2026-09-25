"""Typed decision signals for offline session-map change detection."""

from __future__ import annotations

from pydantic import BaseModel, Field

from core.llm.decision import DecisionRequest

SESSION_DELTA_PROMPT_CONTRACT_VERSION = "session-map-change-v1"
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

_SHARED_INSTRUCTIONS = """
Judge only durable session-state changes explicitly established by the supplied
canonical conversation delta. A change includes adding, revising, resolving,
rejecting, cancelling, superseding, or materially advancing the named state.
Do not treat a suggestion as adopted, a planned action as completed, file
creation as verification, or a failed/uncommitted attempt as canonical state.
Return independent raw yes-probabilities for every question.
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
