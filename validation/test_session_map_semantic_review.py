"""Contracts for deterministic session-map semantic review metrics."""

from datetime import UTC, datetime

import pytest

from core.memory.session_map.models import (
    EpistemicStatus,
    GoalEntry,
    GoalStatus,
    ObservationEntry,
    Relevance,
    SessionMap,
    SourceRef,
)
from validation.core.session_map_semantic_review import (
    ExpectedEntryJudgment,
    SessionMapSemanticReview,
    SourceSupportJudgment,
    calculate_session_map_semantic_metrics,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _session_map() -> SessionMap:
    return SessionMap.empty(session_id="semantic-review", created_at=NOW).model_copy(
        update={
            "goals": (
                GoalEntry(
                    id="goal_release",
                    text="Prepare and publish the release note.",
                    status=GoalStatus.ACTIVE,
                    source_refs=(SourceRef(sequence_index=0, role="user"),),
                ),
            ),
            "observations": (
                ObservationEntry(
                    id="observation_build",
                    text="The build passed.",
                    epistemic_status=EpistemicStatus.OBSERVED,
                    relevance=Relevance.REQUIRED_FOR_ACTIVE_WORK,
                    source_refs=(SourceRef(sequence_index=1, role="tool"),),
                ),
            ),
            "updated_through_sequence_index": 1,
        }
    )


def _expected_map() -> dict[str, object]:
    return {
        "entries": [
            {"id": "expected_prepare", "dimension": "goals"},
            {"id": "expected_publish", "dimension": "work_items"},
        ]
    }


def test_semantic_metrics_allow_one_compact_entry_to_cover_multiple_concepts() -> None:
    metrics = calculate_session_map_semantic_metrics(
        session_map=_session_map(),
        expected_final_map=_expected_map(),
        review=SessionMapSemanticReview(
            expected_entries=(
                ExpectedEntryJudgment(
                    expected_entry_id="expected_prepare",
                    actual_entry_ids=("goal_release",),
                    meaning_preserved=True,
                    lifecycle_preserved=True,
                    provenance_preserved=True,
                ),
                ExpectedEntryJudgment(
                    expected_entry_id="expected_publish",
                    actual_entry_ids=("goal_release",),
                    meaning_preserved=True,
                    lifecycle_preserved=False,
                    provenance_preserved=True,
                ),
            ),
            unsupported_actual_entry_ids=("observation_build",),
            unsupported_source_refs=(
                SourceSupportJudgment(
                    actual_entry_id="observation_build", sequence_index=1
                ),
            ),
            stale_active_actual_entry_ids=("goal_release",),
            unchanged_entry_checks=4,
            unchanged_entry_mutations=1,
            attention_correct=True,
        ),
    )
    assert metrics.required_entry_recall == 0.5
    assert metrics.unsupported_entry_rate == 0.5
    assert metrics.source_reference_precision == 0.5
    assert metrics.stale_active_rate == 0.5
    assert metrics.unchanged_entry_mutation_rate == 0.25
    assert metrics.unsupported_state_promotions == 0
    assert metrics.attention_correct is True


def test_semantic_review_requires_complete_expected_coverage_and_known_ids() -> None:
    with pytest.raises(ValueError, match="must judge every expected entry"):
        calculate_session_map_semantic_metrics(
            session_map=_session_map(),
            expected_final_map=_expected_map(),
            review=SessionMapSemanticReview(expected_entries=()),
        )

    judgments = tuple(
        ExpectedEntryJudgment(
            expected_entry_id=expected_id,
            actual_entry_ids=("unknown",),
            meaning_preserved=True,
            lifecycle_preserved=True,
            provenance_preserved=True,
        )
        for expected_id in ("expected_prepare", "expected_publish")
    )
    with pytest.raises(ValueError, match="unknown entries"):
        calculate_session_map_semantic_metrics(
            session_map=_session_map(),
            expected_final_map=_expected_map(),
            review=SessionMapSemanticReview(expected_entries=judgments),
        )
