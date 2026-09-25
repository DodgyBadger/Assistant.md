"""Labelled live probe for session-map change detection with Jev."""

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
    SESSION_DELTA_PROMPT_CONTRACT_VERSION,
    SESSION_MAP_DIMENSIONS,
    build_session_delta_request,
)
from core.settings.secrets_store import set_secret_value  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402
from validation.core.live_session_memory_corpus import (  # noqa: E402
    load_live_session_memory_corpus,
)

CALIBRATION_CASE_IDS = {
    "research_memo_corrections",
    "read_only_migration_diagnosis",
}
HOLDOUT_CASE_IDS = {"goal_switch_and_cancellation"}
CONSEQUENTIAL_DIMENSIONS = {
    "goals",
    "decisions",
    "constraints",
    "commitments",
    "open_questions",
}
THRESHOLDS = tuple(value / 100 for value in range(10, 95, 5))


class SessionMapChangeDetectionProbeScenario(BaseScenario):
    """Measure a configured Jev model against frozen classifier labels."""

    async def test_scenario(self) -> None:
        api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "Set TYPESAFE_API_KEY only for this opt-in live experiment. "
                "Deterministic validation does not require it."
            )

        corpus = load_live_session_memory_corpus()
        case_ids = {case["id"] for case in corpus["cases"]}
        expected_ids = CALIBRATION_CASE_IDS | HOLDOUT_CASE_IDS
        if case_ids != expected_ids:
            raise RuntimeError("classifier split no longer matches the frozen corpus")

        await self.start_system()
        records: list[dict[str, Any]] = []
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            set_secret_value("TYPESAFE_API_KEY", api_key)
            classifier = build_decision_classifier("jev")
            for case in corpus["cases"]:
                for batch_index, batch in enumerate(case["classifier_batches"]):
                    delta = _render_delta(case["messages"], batch)
                    result = await classifier.classify(
                        build_session_delta_request(delta)
                    )
                    records.append(
                        {
                            "case_id": case["id"],
                            "split": (
                                "calibration"
                                if case["id"] in CALIBRATION_CASE_IDS
                                else "holdout"
                            ),
                            "batch_index": batch_index,
                            "from_sequence_index": batch["from_sequence_index"],
                            "through_sequence_index": batch["through_sequence_index"],
                            "expected_changed_dimensions": batch["changed_dimensions"],
                            "probabilities": result.output.model_dump(),
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
        consequential_recall = _recall_for_dimensions(
            holdout, threshold, CONSEQUENTIAL_DIMENSIONS
        )
        latencies = [float(item["latency_seconds"]) for item in records]
        total_input_tokens = sum(
            int(item["usage"]["input_tokens"] or 0) for item in records
        )
        summary = {
            "prompt_contract_version": SESSION_DELTA_PROMPT_CONTRACT_VERSION,
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
            "records": records,
        }
        (self.artifacts_dir / "session_map_change_detection.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.soft_assert_equal(
            consequential_recall,
            1.0,
            "Held-out consequential changes must have no false negatives",
        )
        self.soft_assert(
            holdout_metrics["macro_recall"] >= 0.80,
            "Held-out macro recall must meet the predeclared gate",
        )
        self.soft_assert(
            holdout_metrics["micro_precision"] >= 0.70,
            "Held-out micro precision must meet the predeclared gate",
        )
        self.soft_assert(
            summary["median_latency_seconds"] <= 1.5,
            "Median classifier latency must meet the predeclared gate",
        )
        self.soft_assert(
            summary["p95_latency_seconds"] <= 3.0,
            "P95 classifier latency must meet the predeclared gate",
        )
        self.soft_assert(
            total_input_tokens <= 5_000,
            "Nine labelled requests must stay within the predeclared token budget",
        )

        self.teardown_scenario()
        self.assert_no_failures()


def _render_delta(messages: list[dict[str, Any]], batch: dict[str, Any]) -> str:
    selected = [
        item
        for item in messages
        if batch["from_sequence_index"]
        <= item["sequence_index"]
        <= batch["through_sequence_index"]
    ]
    lines = [
        f"[{item['sequence_index']}] {item['kind']}: {item['content']}"
        for item in selected
    ]
    return "\n".join(lines)


def _select_threshold(
    records: list[dict[str, Any]],
) -> tuple[float, dict[str, float]]:
    candidates: list[tuple[float, float, dict[str, float]]] = []
    for threshold in THRESHOLDS:
        metrics = _score(records, threshold)
        if metrics["micro_precision"] < 0.65:
            continue
        precision = metrics["micro_precision"]
        recall = metrics["micro_recall"]
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


def _score(records: list[dict[str, Any]], threshold: float) -> dict[str, float]:
    true_positive = false_positive = false_negative = 0
    recalls: list[float] = []
    for dimension in SESSION_MAP_DIMENSIONS:
        dimension_tp = dimension_fn = 0
        for record in records:
            expected = dimension in record["expected_changed_dimensions"]
            predicted = float(record["probabilities"][dimension]) >= threshold
            if expected and predicted:
                true_positive += 1
                dimension_tp += 1
            elif expected:
                false_negative += 1
                dimension_fn += 1
            elif predicted:
                false_positive += 1
        if dimension_tp + dimension_fn:
            recalls.append(dimension_tp / (dimension_tp + dimension_fn))
    return {
        "micro_precision": _ratio(true_positive, true_positive + false_positive),
        "micro_recall": _ratio(true_positive, true_positive + false_negative),
        "macro_recall": statistics.fmean(recalls),
    }


def _recall_for_dimensions(
    records: list[dict[str, Any]], threshold: float, dimensions: set[str]
) -> float:
    true_positive = false_negative = 0
    for record in records:
        for dimension in dimensions:
            if dimension not in record["expected_changed_dimensions"]:
                continue
            if float(record["probabilities"][dimension]) >= threshold:
                true_positive += 1
            else:
                false_negative += 1
    return _ratio(true_positive, true_positive + false_negative)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0
