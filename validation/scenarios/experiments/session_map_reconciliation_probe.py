"""Labelled live probe for broad session-map reconciliation gating."""

from __future__ import annotations

import json
import math
import os
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority  # noqa: E402
from core.llm.decision import build_decision_classifier  # noqa: E402
from core.memory.session_map.change_detection import (  # noqa: E402
    SESSION_RECONCILIATION_PROMPT_CONTRACT_VERSION,
    build_session_reconciliation_request,
)
from core.settings.secrets_store import set_secret_value  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402
from validation.core.live_session_memory_corpus import (  # noqa: E402
    load_session_reconciliation_corpus,
)

CALIBRATION_CASE_IDS = {"project_delivery", "diagnosis"}
HOLDOUT_CASE_IDS = {"editorial_pivot", "release_investigation"}
THRESHOLDS = tuple(value / 100 for value in range(10, 95, 5))
HARD_CADENCE_BATCHES = 3


class SessionMapReconciliationProbeScenario(BaseScenario):
    """Measure one broad scheduling question against a frozen v2 holdout."""

    async def test_scenario(self) -> None:
        api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "Set TYPESAFE_API_KEY only for this opt-in live experiment. "
                "Deterministic validation does not require it."
            )

        corpus = load_session_reconciliation_corpus()
        _validate_corpus(corpus)
        await self.start_system()
        records: list[dict[str, Any]] = []
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            set_secret_value("TYPESAFE_API_KEY", api_key)
            classifier = build_decision_classifier("jev")
            for case in corpus["cases"]:
                for batch_index, batch in enumerate(case["batches"]):
                    result = await classifier.classify(
                        build_session_reconciliation_request(
                            batch["current_map"], batch["canonical_delta"]
                        )
                    )
                    records.append(
                        {
                            "case_id": case["id"],
                            "split": case["split"],
                            "batch_index": batch_index,
                            "phenomenon": batch["phenomenon"],
                            "expected_reconciliation_needed": batch[
                                "expected_reconciliation_needed"
                            ],
                            "consequential": batch["consequential"],
                            "probability": result.output.reconciliation_needed,
                            "requested_model_alias": result.requested_model_alias,
                            "resolved_model_name": result.resolved_model_name,
                            "provider_name": result.provider_name,
                            "latency_seconds": result.latency_seconds,
                            "usage": {
                                "requests": result.usage.requests,
                                "input_tokens": result.usage.input_tokens,
                                "output_tokens": result.usage.output_tokens,
                            },
                        }
                    )

        calibration = [item for item in records if item["split"] == "calibration"]
        holdout = [item for item in records if item["split"] == "holdout"]
        threshold, calibration_metrics = _select_threshold(calibration)
        holdout_metrics = _score(holdout, threshold)
        consequential_recall = _consequential_recall(holdout, threshold)
        latencies = [float(item["latency_seconds"]) for item in records]
        total_input_tokens = sum(
            int(item["usage"]["input_tokens"] or 0) for item in records
        )
        scheduling = _scheduling_comparison(records, threshold)
        summary = {
            "prompt_contract_version": (SESSION_RECONCILIATION_PROMPT_CONTRACT_VERSION),
            "corpus_version": corpus["corpus_version"],
            "calibration_case_ids": sorted(CALIBRATION_CASE_IDS),
            "holdout_case_ids": sorted(HOLDOUT_CASE_IDS),
            "selected_global_threshold": threshold,
            "calibration_metrics": calibration_metrics,
            "holdout_metrics": holdout_metrics,
            "holdout_consequential_recall": consequential_recall,
            "median_latency_seconds": statistics.median(latencies),
            "p95_latency_seconds": sorted(latencies)[
                math.ceil(0.95 * len(latencies)) - 1
            ],
            "total_input_tokens": total_input_tokens,
            "scheduling_comparison": scheduling,
            "records": records,
        }
        (self.artifacts_dir / "session_map_reconciliation.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.soft_assert_equal(
            consequential_recall,
            1.0,
            "Held-out consequential changes must have no false negatives",
        )
        self.soft_assert(
            holdout_metrics["recall"] >= 0.90,
            "Held-out recall must meet the predeclared gate",
        )
        self.soft_assert(
            holdout_metrics["precision"] >= 0.80,
            "Held-out precision must meet the predeclared gate",
        )
        self.soft_assert(
            summary["median_latency_seconds"] <= 1.0,
            "Median classifier latency must meet the predeclared gate",
        )
        self.soft_assert(
            summary["p95_latency_seconds"] <= 2.0,
            "P95 classifier latency must meet the predeclared gate",
        )
        self.soft_assert(
            total_input_tokens <= 8_000,
            "Twenty labelled requests must stay within the predeclared token budget",
        )

        self.teardown_scenario()
        self.assert_no_failures()


def _validate_corpus(corpus: dict[str, Any]) -> None:
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("reconciliation corpus cases must be a list")
    case_ids = {case.get("id") for case in cases}
    if case_ids != CALIBRATION_CASE_IDS | HOLDOUT_CASE_IDS:
        raise RuntimeError("reconciliation split no longer matches the frozen corpus")
    if sum(len(case.get("batches", [])) for case in cases) != 20:
        raise RuntimeError("reconciliation corpus must contain twenty frozen batches")
    for case in cases:
        if case.get("split") not in {"calibration", "holdout"}:
            raise RuntimeError("each reconciliation case must declare its split")
        expected_split = (
            "calibration" if case["id"] in CALIBRATION_CASE_IDS else "holdout"
        )
        if case["split"] != expected_split:
            raise RuntimeError("reconciliation case is in the wrong frozen split")
        for batch in case["batches"]:
            required = {
                "current_map",
                "canonical_delta",
                "expected_reconciliation_needed",
                "consequential",
                "phenomenon",
            }
            if set(batch) != required:
                raise RuntimeError("reconciliation batch schema changed")
            if batch["consequential"] and not batch["expected_reconciliation_needed"]:
                raise RuntimeError("a consequential batch must require reconciliation")


def _select_threshold(
    records: list[dict[str, Any]],
) -> tuple[float, dict[str, float | int]]:
    candidates: list[tuple[float, float, dict[str, float | int]]] = []
    for threshold in THRESHOLDS:
        metrics = _score(records, threshold)
        if metrics["precision"] < 0.75:
            continue
        precision = float(metrics["precision"])
        recall = float(metrics["recall"])
        f2 = (
            0.0
            if 4 * precision + recall == 0
            else 5 * precision * recall / (4 * precision + recall)
        )
        candidates.append((f2, threshold, metrics))
    if not candidates:
        raise RuntimeError("no calibration threshold meets minimum precision")
    _f2, threshold, metrics = max(candidates, key=lambda item: (item[0], item[1]))
    return threshold, metrics


def _score(records: list[dict[str, Any]], threshold: float) -> dict[str, float | int]:
    true_positive = false_positive = true_negative = false_negative = 0
    for record in records:
        expected = bool(record["expected_reconciliation_needed"])
        predicted = float(record["probability"]) >= threshold
        if expected and predicted:
            true_positive += 1
        elif expected:
            false_negative += 1
        elif predicted:
            false_positive += 1
        else:
            true_negative += 1
    return {
        "precision": _ratio(true_positive, true_positive + false_positive),
        "recall": _ratio(true_positive, true_positive + false_negative),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
    }


def _consequential_recall(records: list[dict[str, Any]], threshold: float) -> float:
    consequential = [item for item in records if item["consequential"]]
    hits = sum(float(item["probability"]) >= threshold for item in consequential)
    return _ratio(hits, len(consequential))


def _scheduling_comparison(
    records: list[dict[str, Any]], threshold: float
) -> dict[str, int]:
    classifier_count = sum(float(item["probability"]) >= threshold for item in records)
    classifier_or_hard_count = sum(
        float(item["probability"]) >= threshold
        or (int(item["batch_index"]) + 1) % HARD_CADENCE_BATCHES == 0
        for item in records
    )
    return {
        "total_batches": len(records),
        "reconcile_every_batch": len(records),
        "classifier_only": classifier_count,
        "classifier_or_hard_cadence": classifier_or_hard_count,
        "hard_cadence_batches": HARD_CADENCE_BATCHES,
        "classifier_calls_avoided": 0,
        "authoring_calls_avoided_classifier_only": len(records) - classifier_count,
        "authoring_calls_avoided_with_hard_cadence": (
            len(records) - classifier_or_hard_count
        ),
    }


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0
