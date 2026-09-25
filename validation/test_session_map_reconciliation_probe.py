"""Deterministic checks for the frozen reconciliation-gating experiment."""

import tempfile
from pathlib import Path

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="assistantmd-reconcile-probe-")
_TEST_ROOT_PATH = Path(_TEST_ROOT.name)
set_bootstrap_roots(_TEST_ROOT_PATH / "data", _TEST_ROOT_PATH / "system")

from validation.core.live_session_memory_corpus import (  # noqa: E402
    load_session_reconciliation_corpus,
)
from validation.scenarios.experiments.session_map_reconciliation_probe import (  # noqa: E402
    _scheduling_comparison,
    _score,
    _select_threshold,
    _validate_corpus,
)


def test_reconciliation_corpus_has_frozen_balanced_splits() -> None:
    corpus = load_session_reconciliation_corpus()
    _validate_corpus(corpus)
    cases = corpus["cases"]
    assert [case["split"] for case in cases].count("calibration") == 2
    assert [case["split"] for case in cases].count("holdout") == 2
    batches = [batch for case in cases for batch in case["batches"]]
    assert sum(batch["expected_reconciliation_needed"] for batch in batches) == 12
    assert sum(not batch["expected_reconciliation_needed"] for batch in batches) == 8


def test_threshold_uses_calibration_probabilities_and_prefers_recall() -> None:
    records = [
        {"expected_reconciliation_needed": True, "probability": 0.85},
        {"expected_reconciliation_needed": True, "probability": 0.55},
        {"expected_reconciliation_needed": False, "probability": 0.50},
        {"expected_reconciliation_needed": False, "probability": 0.10},
    ]
    threshold, metrics = _select_threshold(records)
    assert threshold == 0.55
    assert metrics == {
        "precision": 1.0,
        "recall": 1.0,
        "true_positive": 2,
        "false_positive": 0,
        "true_negative": 2,
        "false_negative": 0,
    }


def test_scheduling_report_keeps_hard_cadence_independent_of_negatives() -> None:
    records = [
        {"probability": 0.1, "batch_index": 0},
        {"probability": 0.1, "batch_index": 1},
        {"probability": 0.1, "batch_index": 2},
        {"probability": 0.9, "batch_index": 3},
        {"probability": 0.1, "batch_index": 4},
    ]
    comparison = _scheduling_comparison(records, 0.5)
    assert comparison["classifier_only"] == 1
    assert comparison["classifier_or_hard_cadence"] == 2
    assert comparison["authoring_calls_avoided_with_hard_cadence"] == 3
    assert (
        _score(
            [
                {
                    "expected_reconciliation_needed": False,
                    "probability": records[0]["probability"],
                }
            ],
            0.5,
        )["true_negative"]
        == 1
    )
