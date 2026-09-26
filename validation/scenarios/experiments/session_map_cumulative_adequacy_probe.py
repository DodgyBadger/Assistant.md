"""Labelled live probe for cumulative per-field session-map adequacy gating."""

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
    SESSION_CUMULATIVE_ADEQUACY_PROMPT_CONTRACT_VERSION,
    SESSION_MAP_ADEQUACY_FIELDS,
    build_cumulative_session_map_adequacy_request,
)
from core.settings.secrets_store import set_secret_value  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402
from validation.core.live_session_memory_corpus import (  # noqa: E402
    load_cumulative_session_map_adequacy_corpus,
)

CALIBRATION_CASE_IDS = {
    "formatting_preferences_accumulate",
    "regional_latency_pattern",
    "export_option_adoption",
    "draft_artifact_created",
}
HOLDOUT_CASE_IDS = {
    "editorial_goal_pivot",
    "vendor_date_resolution",
    "faq_commitment",
    "migration_blocker_accumulates",
}
THRESHOLDS = tuple(value / 100 for value in range(10, 100, 5))


class SessionMapCumulativeAdequacyProbeScenario(BaseScenario):
    """Measure cumulative field stability against a frozen v3 holdout."""

    async def test_scenario(self) -> None:
        api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "Set TYPESAFE_API_KEY only for this opt-in live experiment. "
                "Deterministic validation does not require it."
            )

        corpus = load_cumulative_session_map_adequacy_corpus()
        _validate_corpus(corpus)
        await self.start_system()
        records: list[dict[str, Any]] = []
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            set_secret_value("TYPESAFE_API_KEY", api_key)
            classifier = build_decision_classifier("jev")
            for case in corpus["cases"]:
                cumulative_parts: list[str] = []
                for turn_index, turn in enumerate(case["turns"], start=1):
                    cumulative_parts.append(turn["canonical_delta_append"])
                    cumulative_delta = "\n".join(cumulative_parts)
                    result = await classifier.classify(
                        build_cumulative_session_map_adequacy_request(
                            case["current_map"], cumulative_delta
                        )
                    )
                    scores = result.output.model_dump()
                    expected_inadequate = set(turn["expected_inadequate_fields"])
                    records.append(
                        {
                            "case_id": case["id"],
                            "split": case["split"],
                            "turn_index": turn_index,
                            "phenomenon": turn["phenomenon"],
                            "expected_inadequate_fields": sorted(expected_inadequate),
                            "expected_reconciliation_needed": bool(expected_inadequate),
                            "consequential": turn["consequential"],
                            "cumulative_segment_count": len(cumulative_parts),
                            "cumulative_character_count": len(cumulative_delta),
                            "adequacy_scores": scores,
                            "minimum_adequacy": min(scores.values()),
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
        holdout_metrics = _score_checkpoints(holdout, threshold)
        field_metrics = _score_fields(holdout, threshold)
        consequential_recall = _consequential_recall(holdout, threshold)
        latencies = [float(item["latency_seconds"]) for item in records]
        input_tokens = [int(item["usage"]["input_tokens"] or 0) for item in records]
        total_input_tokens = sum(input_tokens)
        scheduling = {
            str(interval): _simulate_eligibility_policy(
                records,
                threshold=threshold,
                eligibility_interval=interval,
                hard_ceiling_turns=int(corpus["hard_ceiling_turns"]),
            )
            for interval in corpus["candidate_eligibility_intervals"]
        }
        acceptance_gate_results = {
            "holdout_consequential_recall": consequential_recall == 1.0,
            "holdout_recall": holdout_metrics["recall"] >= 0.90,
            "holdout_precision": holdout_metrics["precision"] >= 0.75,
            "inadequate_field_macro_recall": (
                field_metrics["macro_inadequate_recall"] >= 0.80
            ),
            "median_latency_seconds": statistics.median(latencies) <= 1.0,
            "p95_latency_seconds": _p95(latencies) <= 2.0,
            "average_input_tokens": statistics.mean(input_tokens) <= 1_500,
            "total_input_tokens": total_input_tokens <= 60_000,
        }
        summary = {
            "prompt_contract_version": (
                SESSION_CUMULATIVE_ADEQUACY_PROMPT_CONTRACT_VERSION
            ),
            "corpus_version": corpus["corpus_version"],
            "calibration_case_ids": sorted(CALIBRATION_CASE_IDS),
            "holdout_case_ids": sorted(HOLDOUT_CASE_IDS),
            "selected_global_skip_threshold": threshold,
            "threshold_semantics": (
                "author when any adequacy score is below the threshold"
            ),
            "calibration_metrics": calibration_metrics,
            "holdout_metrics": holdout_metrics,
            "holdout_field_metrics": field_metrics,
            "holdout_consequential_recall": consequential_recall,
            "median_latency_seconds": statistics.median(latencies),
            "p95_latency_seconds": _p95(latencies),
            "average_input_tokens": statistics.mean(input_tokens),
            "total_input_tokens": total_input_tokens,
            "scheduling_comparison": scheduling,
            "acceptance_gate_results": acceptance_gate_results,
            "gate_outcome": {
                "passed": all(acceptance_gate_results.values()),
                "failed_gates": [
                    name
                    for name, passed in acceptance_gate_results.items()
                    if not passed
                ],
            },
            "records": records,
        }
        (self.artifacts_dir / "session_map_cumulative_adequacy.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        for gate_name, passed in acceptance_gate_results.items():
            self.soft_assert(passed, f"Cumulative adequacy gate failed: {gate_name}")

        self.teardown_scenario()
        self.assert_no_failures()


def _validate_corpus(corpus: dict[str, Any]) -> None:
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("cumulative adequacy corpus cases must be a list")
    case_ids = {case.get("id") for case in cases}
    if case_ids != CALIBRATION_CASE_IDS | HOLDOUT_CASE_IDS:
        raise RuntimeError("cumulative adequacy split no longer matches the corpus")
    if corpus.get("candidate_eligibility_intervals") != [1, 2, 3]:
        raise RuntimeError("candidate eligibility intervals changed")
    if corpus.get("hard_ceiling_turns") != 5:
        raise RuntimeError("hard ceiling changed")
    dimension_fields = set(SESSION_MAP_ADEQUACY_FIELDS) - {"coverage_adequate"}
    for case in cases:
        expected_split = (
            "calibration" if case["id"] in CALIBRATION_CASE_IDS else "holdout"
        )
        if case.get("split") != expected_split:
            raise RuntimeError("cumulative adequacy case is in the wrong split")
        if not str(case.get("current_map", "")).strip():
            raise RuntimeError("each case requires one accepted current map")
        turns = case.get("turns")
        if not isinstance(turns, list) or len(turns) != 5:
            raise RuntimeError("each cumulative adequacy case requires five turns")
        previous_inadequate: set[str] = set()
        for turn in turns:
            if set(turn) != {
                "canonical_delta_append",
                "expected_inadequate_fields",
                "consequential",
                "phenomenon",
            }:
                raise RuntimeError("cumulative adequacy turn schema changed")
            if not str(turn["canonical_delta_append"]).strip():
                raise RuntimeError("cumulative delta append must not be empty")
            inadequate = set(turn["expected_inadequate_fields"])
            if not inadequate <= dimension_fields:
                raise RuntimeError("turn names an unknown adequacy field")
            if not previous_inadequate <= inadequate:
                raise RuntimeError("unreconciled inadequate fields cannot disappear")
            if turn["consequential"] and not inadequate:
                raise RuntimeError("a consequential turn must require reconciliation")
            previous_inadequate = inadequate
        if not previous_inadequate:
            raise RuntimeError("each epoch must eventually require reconciliation")


def _select_threshold(
    records: list[dict[str, Any]],
) -> tuple[float, dict[str, float | int]]:
    candidates: list[tuple[float, float, dict[str, float | int]]] = []
    for threshold in THRESHOLDS:
        metrics = _score_checkpoints(records, threshold)
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
        raise RuntimeError("no adequacy threshold meets minimum precision")
    _f2, threshold, metrics = max(candidates, key=lambda item: (item[0], item[1]))
    return threshold, metrics


def _score_checkpoints(
    records: list[dict[str, Any]], threshold: float
) -> dict[str, float | int]:
    true_positive = false_positive = true_negative = false_negative = 0
    for record in records:
        expected = bool(record["expected_reconciliation_needed"])
        predicted = float(record["minimum_adequacy"]) < threshold
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


def _score_fields(records: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    metrics: dict[str, dict[str, float | int]] = {}
    recalls: list[float] = []
    for field_name in SESSION_MAP_ADEQUACY_FIELDS:
        true_positive = false_positive = true_negative = false_negative = 0
        for record in records:
            expected_inadequate = (
                bool(record["expected_reconciliation_needed"])
                if field_name == "coverage_adequate"
                else field_name in record["expected_inadequate_fields"]
            )
            predicted_inadequate = (
                float(record["adequacy_scores"][field_name]) < threshold
            )
            if expected_inadequate and predicted_inadequate:
                true_positive += 1
            elif expected_inadequate:
                false_negative += 1
            elif predicted_inadequate:
                false_positive += 1
            else:
                true_negative += 1
        field_result = {
            "precision": _ratio(true_positive, true_positive + false_positive),
            "recall": _ratio(true_positive, true_positive + false_negative),
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
        }
        metrics[field_name] = field_result
        if true_positive + false_negative:
            recalls.append(float(field_result["recall"]))
    return {
        "by_field": metrics,
        "macro_inadequate_recall": statistics.mean(recalls) if recalls else 1.0,
    }


def _consequential_recall(records: list[dict[str, Any]], threshold: float) -> float:
    consequential = [record for record in records if record["consequential"]]
    hits = sum(
        float(record["minimum_adequacy"]) < threshold for record in consequential
    )
    return _ratio(hits, len(consequential))


def _simulate_eligibility_policy(
    records: list[dict[str, Any]],
    *,
    threshold: float,
    eligibility_interval: int,
    hard_ceiling_turns: int,
) -> dict[str, int]:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_case.setdefault(str(record["case_id"]), []).append(record)
    classifier_calls = classifier_triggers = hard_ceiling_triggers = 0
    false_early_triggers = maximum_detection_lag = 0
    for case_records in by_case.values():
        ordered = sorted(case_records, key=lambda item: int(item["turn_index"]))
        change_turn = next(
            int(item["turn_index"])
            for item in ordered
            if item["expected_reconciliation_needed"]
        )
        for record in ordered:
            turn_index = int(record["turn_index"])
            if (
                turn_index % eligibility_interval != 0
                and turn_index != hard_ceiling_turns
            ):
                continue
            classifier_calls += 1
            classifier_triggered = float(record["minimum_adequacy"]) < threshold
            if classifier_triggered:
                classifier_triggers += 1
                false_early_triggers += turn_index < change_turn
                maximum_detection_lag = max(
                    maximum_detection_lag, max(0, turn_index - change_turn)
                )
                break
            if turn_index == hard_ceiling_turns:
                hard_ceiling_triggers += 1
                maximum_detection_lag = max(
                    maximum_detection_lag, max(0, turn_index - change_turn)
                )
                break
    return {
        "cases": len(by_case),
        "classifier_calls": classifier_calls,
        "classifier_triggers": classifier_triggers,
        "hard_ceiling_triggers": hard_ceiling_triggers,
        "false_early_triggers": false_early_triggers,
        "maximum_detection_lag_turns": maximum_detection_lag,
    }


def _p95(values: list[float]) -> float:
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0
