"""Deterministic checks for cumulative session-map adequacy gating."""

import tempfile
from pathlib import Path

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="assistantmd-cumulative-adequacy-")
_TEST_ROOT_PATH = Path(_TEST_ROOT.name)
set_bootstrap_roots(_TEST_ROOT_PATH / "data", _TEST_ROOT_PATH / "system")

from core.memory.session_map.change_detection import (  # noqa: E402
    SESSION_MAP_ADEQUACY_FIELDS,
)
from validation.core.live_session_memory_corpus import (  # noqa: E402
    load_cumulative_session_map_adequacy_corpus,
)
from validation.scenarios.experiments.session_map_cumulative_adequacy_probe import (  # noqa: E402
    _score_fields,
    _select_threshold,
    _simulate_eligibility_policy,
    _validate_corpus,
)
from validation.scenarios.experiments.session_map_cumulative_change_calibration import (  # noqa: E402
    _select_threshold as _select_change_threshold,
)


def _record(
    *,
    case_id: str,
    turn_index: int,
    minimum_adequacy: float,
    expected_reconciliation: bool,
    inadequate_fields: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "turn_index": turn_index,
        "minimum_adequacy": minimum_adequacy,
        "expected_reconciliation_needed": expected_reconciliation,
        "expected_inadequate_fields": list(inadequate_fields),
        "consequential": expected_reconciliation,
        "adequacy_scores": {
            field_name: (
                minimum_adequacy
                if field_name in inadequate_fields or field_name == "coverage_adequate"
                else 0.95
            )
            for field_name in SESSION_MAP_ADEQUACY_FIELDS
        },
    }


def test_cumulative_corpus_freezes_monotonic_five_turn_epochs() -> None:
    corpus = load_cumulative_session_map_adequacy_corpus()
    _validate_corpus(corpus)
    assert len(corpus["cases"]) == 8
    assert sum(case["split"] == "calibration" for case in corpus["cases"]) == 4
    assert sum(case["split"] == "holdout" for case in corpus["cases"]) == 4
    for case in corpus["cases"]:
        cumulative_parts: list[str] = []
        previous_inadequate: set[str] = set()
        for turn in case["turns"]:
            cumulative_parts.append(turn["canonical_delta_append"])
            assert "\n".join(cumulative_parts).endswith(turn["canonical_delta_append"])
            inadequate = set(turn["expected_inadequate_fields"])
            assert previous_inadequate <= inadequate
            previous_inadequate = inadequate


def test_threshold_uses_low_adequacy_as_reconciliation_signal() -> None:
    records = [
        _record(
            case_id="case",
            turn_index=1,
            minimum_adequacy=0.90,
            expected_reconciliation=False,
        ),
        _record(
            case_id="case",
            turn_index=2,
            minimum_adequacy=0.70,
            expected_reconciliation=False,
        ),
        _record(
            case_id="case",
            turn_index=3,
            minimum_adequacy=0.65,
            expected_reconciliation=True,
        ),
        _record(
            case_id="case",
            turn_index=4,
            minimum_adequacy=0.20,
            expected_reconciliation=True,
        ),
    ]
    threshold, metrics = _select_threshold(records)
    assert threshold == 0.70
    assert metrics == {
        "precision": 1.0,
        "recall": 1.0,
        "true_positive": 2,
        "false_positive": 0,
        "true_negative": 2,
        "false_negative": 0,
    }


def test_field_scoring_treats_coverage_as_global_inadequacy() -> None:
    records = [
        _record(
            case_id="case",
            turn_index=1,
            minimum_adequacy=0.20,
            expected_reconciliation=True,
            inadequate_fields=("goals_adequate",),
        )
    ]
    metrics = _score_fields(records, 0.50)
    assert metrics["by_field"]["goals_adequate"]["recall"] == 1.0
    assert metrics["by_field"]["coverage_adequate"]["recall"] == 1.0
    assert metrics["by_field"]["constraints_adequate"]["false_positive"] == 0


def test_eligibility_simulation_reuses_pending_change_until_later_check() -> None:
    records = [
        _record(
            case_id="detected",
            turn_index=index,
            minimum_adequacy=(0.90 if index < 3 else 0.20),
            expected_reconciliation=index >= 3,
            inadequate_fields=("goals_adequate",) if index >= 3 else (),
        )
        for index in range(1, 6)
    ]
    interval_two = _simulate_eligibility_policy(
        records,
        threshold=0.50,
        eligibility_interval=2,
        hard_ceiling_turns=5,
    )
    assert interval_two == {
        "cases": 1,
        "classifier_calls": 2,
        "classifier_triggers": 1,
        "hard_ceiling_triggers": 0,
        "false_early_triggers": 0,
        "maximum_detection_lag_turns": 1,
    }

    interval_three = _simulate_eligibility_policy(
        records,
        threshold=0.50,
        eligibility_interval=3,
        hard_ceiling_turns=5,
    )
    assert interval_three["classifier_calls"] == 1
    assert interval_three["classifier_triggers"] == 1
    assert interval_three["maximum_detection_lag_turns"] == 0


def test_hard_ceiling_forces_authoring_after_false_stable_checks() -> None:
    records = [
        _record(
            case_id="forced",
            turn_index=index,
            minimum_adequacy=0.90,
            expected_reconciliation=index >= 4,
            inadequate_fields=("work_items_adequate",) if index >= 4 else (),
        )
        for index in range(1, 6)
    ]
    result = _simulate_eligibility_policy(
        records,
        threshold=0.50,
        eligibility_interval=3,
        hard_ceiling_turns=5,
    )
    assert result == {
        "cases": 1,
        "classifier_calls": 2,
        "classifier_triggers": 0,
        "hard_ceiling_triggers": 1,
        "false_early_triggers": 0,
        "maximum_detection_lag_turns": 1,
    }


def test_direct_change_threshold_uses_high_probability_as_trigger() -> None:
    records = [
        {"expected_reconciliation_needed": False, "broad_probability": 0.20},
        {"expected_reconciliation_needed": False, "broad_probability": 0.30},
        {"expected_reconciliation_needed": True, "broad_probability": 0.35},
        {"expected_reconciliation_needed": True, "broad_probability": 0.80},
    ]
    result = _select_change_threshold(records, probability_key="broad_probability")
    assert result == {
        "threshold": 0.35,
        "metrics": {
            "precision": 1.0,
            "recall": 1.0,
            "true_positive": 2,
            "false_positive": 0,
            "true_negative": 2,
            "false_negative": 0,
        },
    }
