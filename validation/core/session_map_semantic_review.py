"""Deterministic metrics for manually judged session-map semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.memory.session_map.models import SessionMap, session_map_entries


@dataclass(frozen=True)
class ExpectedEntryJudgment:
    """Judge one expected concept without requiring an exact entry shape or ID."""

    expected_entry_id: str
    actual_entry_ids: tuple[str, ...]
    meaning_preserved: bool
    lifecycle_preserved: bool
    provenance_preserved: bool

    @property
    def passed(self) -> bool:
        """Return whether the expected concept is fully preserved."""
        return bool(self.actual_entry_ids) and all(
            (
                self.meaning_preserved,
                self.lifecycle_preserved,
                self.provenance_preserved,
            )
        )


@dataclass(frozen=True)
class SourceSupportJudgment:
    """Identify one emitted source reference that does not support its entry."""

    actual_entry_id: str
    sequence_index: int


@dataclass(frozen=True)
class SessionMapSemanticReview:
    """Human semantic judgments consumed by deterministic metric calculation."""

    expected_entries: tuple[ExpectedEntryJudgment, ...]
    unsupported_actual_entry_ids: tuple[str, ...] = ()
    unsupported_source_refs: tuple[SourceSupportJudgment, ...] = ()
    stale_active_actual_entry_ids: tuple[str, ...] = ()
    unsupported_state_promotion_actual_entry_ids: tuple[str, ...] = ()
    unchanged_entry_checks: int = 0
    unchanged_entry_mutations: int = 0
    attention_correct: bool = False


@dataclass(frozen=True)
class SessionMapSemanticMetrics:
    """Calculated semantic quality metrics for one final authored map."""

    required_entry_recall: float
    unsupported_entry_rate: float
    source_reference_precision: float
    stale_active_rate: float
    unchanged_entry_mutation_rate: float
    unsupported_state_promotions: int
    attention_correct: bool


def calculate_session_map_semantic_metrics(
    *,
    session_map: SessionMap,
    expected_final_map: dict[str, Any],
    review: SessionMapSemanticReview,
) -> SessionMapSemanticMetrics:
    """Validate a manual review and calculate the frozen semantic metrics."""
    actual_entries = {entry.id: entry for entry in session_map_entries(session_map)}
    expected_ids = {str(entry["id"]) for entry in expected_final_map.get("entries", ())}
    judged_expected_ids = {
        judgment.expected_entry_id for judgment in review.expected_entries
    }
    if judged_expected_ids != expected_ids:
        missing = sorted(expected_ids - judged_expected_ids)
        extra = sorted(judged_expected_ids - expected_ids)
        raise ValueError(
            f"semantic review must judge every expected entry; missing={missing}, "
            f"extra={extra}"
        )
    if len(judged_expected_ids) != len(review.expected_entries):
        raise ValueError("semantic review contains duplicate expected-entry judgments")

    actual_ids = set(actual_entries)
    referenced_actual_ids = {
        actual_id
        for judgment in review.expected_entries
        for actual_id in judgment.actual_entry_ids
    }
    _require_actual_ids("expected-entry match", referenced_actual_ids, actual_ids)
    unsupported_ids = set(review.unsupported_actual_entry_ids)
    stale_ids = set(review.stale_active_actual_entry_ids)
    promotion_ids = set(review.unsupported_state_promotion_actual_entry_ids)
    _require_actual_ids("unsupported entry", unsupported_ids, actual_ids)
    _require_actual_ids("stale active entry", stale_ids, actual_ids)
    _require_actual_ids("unsupported state promotion", promotion_ids, actual_ids)
    _require_unique("unsupported entry", review.unsupported_actual_entry_ids)
    _require_unique("stale active entry", review.stale_active_actual_entry_ids)
    _require_unique(
        "unsupported state promotion",
        review.unsupported_state_promotion_actual_entry_ids,
    )

    source_refs = {
        (entry.id, ref.sequence_index)
        for entry in actual_entries.values()
        for ref in (*entry.source_refs, *entry.state_source_refs)
    }
    unsupported_source_refs = {
        (judgment.actual_entry_id, judgment.sequence_index)
        for judgment in review.unsupported_source_refs
    }
    if len(unsupported_source_refs) != len(review.unsupported_source_refs):
        raise ValueError("semantic review contains duplicate unsupported source refs")
    unknown_refs = sorted(unsupported_source_refs - source_refs)
    if unknown_refs:
        raise ValueError(
            f"unsupported source judgments reference absent refs: {unknown_refs}"
        )
    if review.unchanged_entry_checks < 0:
        raise ValueError("unchanged entry checks cannot be negative")
    if not 0 <= review.unchanged_entry_mutations <= review.unchanged_entry_checks:
        raise ValueError("unchanged entry mutations must be within checked entries")

    active_ids = {
        entry.id for entry in actual_entries.values() if _entry_is_active(entry)
    }
    if not stale_ids <= active_ids:
        raise ValueError("stale-active judgments must identify active actual entries")

    return SessionMapSemanticMetrics(
        required_entry_recall=_ratio(
            sum(judgment.passed for judgment in review.expected_entries),
            len(review.expected_entries),
        ),
        unsupported_entry_rate=_ratio(len(unsupported_ids), len(actual_entries)),
        source_reference_precision=_ratio(
            len(source_refs) - len(unsupported_source_refs), len(source_refs)
        ),
        stale_active_rate=_ratio(len(stale_ids), len(active_ids)),
        unchanged_entry_mutation_rate=_ratio(
            review.unchanged_entry_mutations, review.unchanged_entry_checks
        ),
        unsupported_state_promotions=len(promotion_ids),
        attention_correct=review.attention_correct,
    )


def _require_actual_ids(label: str, selected: set[str], actual: set[str]) -> None:
    unknown = sorted(selected - actual)
    if unknown:
        raise ValueError(f"{label} judgments reference unknown entries: {unknown}")


def _require_unique(label: str, values: tuple[str, ...]) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"semantic review contains duplicate {label} judgments")


def _entry_is_active(entry: Any) -> bool:
    status = getattr(entry, "status", None)
    return status is None or str(status) in {
        "active",
        "blocked",
        "in_progress",
        "open",
        "planned",
        "proposed",
    }


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0
