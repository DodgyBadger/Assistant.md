"""Calibration-only live comparison for direct cumulative Jev change signals."""

from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority  # noqa: E402
from core.llm.decision import build_decision_classifier  # noqa: E402
from core.memory.session_map.change_detection import (  # noqa: E402
    SESSION_CUMULATIVE_CHANGE_PROMPT_CONTRACT_VERSION,
    build_cumulative_session_map_change_request,
)
from core.settings.secrets_store import set_secret_value  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402
from validation.core.live_session_memory_corpus import (  # noqa: E402
    load_cumulative_session_map_adequacy_corpus,
)
from validation.scenarios.experiments.session_map_cumulative_adequacy_probe import (  # noqa: E402
    CALIBRATION_CASE_IDS,
    THRESHOLDS,
    _p95,
    _ratio,
    _validate_corpus,
)


class SessionMapCumulativeChangeCalibrationScenario(BaseScenario):
    """Compare broad and per-field direct change signals on calibration only."""

    async def test_scenario(self) -> None:
        api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Set TYPESAFE_API_KEY for this opt-in live experiment.")
        corpus = load_cumulative_session_map_adequacy_corpus()
        _validate_corpus(corpus)
        await self.start_system()
        records: list[dict[str, Any]] = []
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            set_secret_value("TYPESAFE_API_KEY", api_key)
            classifier = build_decision_classifier("jev")
            for case in corpus["cases"]:
                if case["id"] not in CALIBRATION_CASE_IDS:
                    continue
                cumulative_parts: list[str] = []
                for turn_index, turn in enumerate(case["turns"], start=1):
                    cumulative_parts.append(turn["canonical_delta_append"])
                    result = await classifier.classify(
                        build_cumulative_session_map_change_request(
                            case["current_map"], "\n".join(cumulative_parts)
                        )
                    )
                    scores = result.output.model_dump()
                    field_scores = {
                        name: value
                        for name, value in scores.items()
                        if name != "reconciliation_needed"
                    }
                    records.append(
                        {
                            "case_id": case["id"],
                            "turn_index": turn_index,
                            "phenomenon": turn["phenomenon"],
                            "expected_reconciliation_needed": bool(
                                turn["expected_inadequate_fields"]
                            ),
                            "consequential": turn["consequential"],
                            "change_scores": scores,
                            "broad_probability": scores["reconciliation_needed"],
                            "maximum_field_probability": max(field_scores.values()),
                            "latency_seconds": result.latency_seconds,
                            "resolved_model_name": result.resolved_model_name,
                            "usage": {
                                "requests": result.usage.requests,
                                "input_tokens": result.usage.input_tokens,
                                "output_tokens": result.usage.output_tokens,
                            },
                        }
                    )
        strategies = {
            "broad_only": "broad_probability",
            "fields_only": "maximum_field_probability",
        }
        comparisons = {
            name: _select_threshold(records, probability_key=probability_key)
            for name, probability_key in strategies.items()
        }
        latencies = [float(record["latency_seconds"]) for record in records]
        inputs = [int(record["usage"]["input_tokens"] or 0) for record in records]
        summary = {
            "prompt_contract_version": (
                SESSION_CUMULATIVE_CHANGE_PROMPT_CONTRACT_VERSION
            ),
            "corpus_version": corpus["corpus_version"],
            "split": "calibration_only",
            "case_ids": sorted(CALIBRATION_CASE_IDS),
            "strategy_comparisons": comparisons,
            "median_latency_seconds": statistics.median(latencies),
            "p95_latency_seconds": _p95(latencies),
            "average_input_tokens": statistics.mean(inputs),
            "total_input_tokens": sum(inputs),
            "records": records,
        }
        (self.artifacts_dir / "session_map_cumulative_change.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for strategy, result in comparisons.items():
            self.soft_assert(
                float(result["metrics"]["recall"]) == 1.0,
                f"Direct-change calibration recall failed: {strategy}",
            )
            self.soft_assert(
                float(result["metrics"]["precision"]) >= 0.75,
                f"Direct-change calibration precision failed: {strategy}",
            )
        self.teardown_scenario()
        self.assert_no_failures()


def _select_threshold(
    records: list[dict[str, Any]], *, probability_key: str
) -> dict[str, Any]:
    candidates: list[tuple[float, float, dict[str, float | int]]] = []
    for threshold in THRESHOLDS:
        metrics = _score(records, threshold, probability_key=probability_key)
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
        raise RuntimeError(f"no {probability_key} threshold meets minimum precision")
    _f2, threshold, metrics = max(candidates, key=lambda item: (item[0], item[1]))
    return {"threshold": threshold, "metrics": metrics}


def _score(
    records: list[dict[str, Any]], threshold: float, *, probability_key: str
) -> dict[str, float | int]:
    true_positive = false_positive = true_negative = false_negative = 0
    for record in records:
        expected = bool(record["expected_reconciliation_needed"])
        predicted = float(record[probability_key]) >= threshold
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
