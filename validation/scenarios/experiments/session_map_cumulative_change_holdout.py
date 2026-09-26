"""Fresh holdout for the calibrated direct cumulative Jev change contract."""

from __future__ import annotations

import json
import os
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
    load_cumulative_session_map_change_holdout,
)
from validation.scenarios.experiments.session_map_cumulative_change_calibration import (  # noqa: E402
    _score,
)

BROAD_THRESHOLD = 0.25
FIELD_THRESHOLD = 0.50


class SessionMapCumulativeChangeHoldoutScenario(BaseScenario):
    """Evaluate frozen V4 thresholds without holdout tuning."""

    async def test_scenario(self) -> None:
        api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Set TYPESAFE_API_KEY for this opt-in live experiment.")
        corpus = load_cumulative_session_map_change_holdout()
        _validate_holdout(corpus)
        await self.start_system()
        records: list[dict[str, Any]] = []
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            set_secret_value("TYPESAFE_API_KEY", api_key)
            classifier = build_decision_classifier("jev")
            for case in corpus["cases"]:
                cumulative: list[str] = []
                for turn_index, turn in enumerate(case["turns"], start=1):
                    cumulative.append(turn[0])
                    result = await classifier.classify(
                        build_cumulative_session_map_change_request(
                            case["current_map"], "\n".join(cumulative)
                        )
                    )
                    scores = result.output.model_dump()
                    records.append(
                        {
                            "case_id": case["id"],
                            "turn_index": turn_index,
                            "expected_reconciliation_needed": turn[1],
                            "consequential": turn[2],
                            "broad_probability": scores["reconciliation_needed"],
                            "maximum_field_probability": max(
                                value
                                for name, value in scores.items()
                                if name != "reconciliation_needed"
                            ),
                            "change_scores": scores,
                            "resolved_model_name": result.resolved_model_name,
                            "latency_seconds": result.latency_seconds,
                            "usage": {
                                "input_tokens": result.usage.input_tokens,
                                "output_tokens": result.usage.output_tokens,
                            },
                        }
                    )
        strategies = {
            "broad_only": _score(
                records, BROAD_THRESHOLD, probability_key="broad_probability"
            ),
            "fields_only": _score(
                records, FIELD_THRESHOLD, probability_key="maximum_field_probability"
            ),
        }
        consequential = [record for record in records if record["consequential"]]
        consequential_recall = {
            "broad_only": sum(
                float(record["broad_probability"]) >= BROAD_THRESHOLD
                for record in consequential
            )
            / len(consequential),
            "fields_only": sum(
                float(record["maximum_field_probability"]) >= FIELD_THRESHOLD
                for record in consequential
            )
            / len(consequential),
        }
        summary = {
            "prompt_contract_version": SESSION_CUMULATIVE_CHANGE_PROMPT_CONTRACT_VERSION,
            "corpus_version": corpus["corpus_version"],
            "frozen_thresholds": {
                "broad_only": BROAD_THRESHOLD,
                "fields_only": FIELD_THRESHOLD,
            },
            "strategy_metrics": strategies,
            "consequential_recall": consequential_recall,
            "records": records,
        }
        (self.artifacts_dir / "session_map_cumulative_change_holdout.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for strategy, metrics in strategies.items():
            self.soft_assert(
                float(metrics["recall"]) >= 0.90,
                f"Fresh holdout recall failed: {strategy}",
            )
            self.soft_assert(
                float(metrics["precision"]) >= 0.75,
                f"Fresh holdout precision failed: {strategy}",
            )
            self.soft_assert_equal(
                consequential_recall[strategy],
                1.0,
                f"Fresh consequential recall failed: {strategy}",
            )
        self.teardown_scenario()
        self.assert_no_failures()


def _validate_holdout(corpus: dict[str, Any]) -> None:
    cases = corpus.get("cases")
    if not isinstance(cases, list) or len(cases) != 4:
        raise RuntimeError("V4 holdout must contain four cases")
    for case in cases:
        if not str(case.get("current_map", "")).strip():
            raise RuntimeError("V4 holdout case requires a current map")
        turns = case.get("turns")
        if not isinstance(turns, list) or len(turns) != 5:
            raise RuntimeError("V4 holdout case requires five turns")
        seen_change = False
        for turn in turns:
            if not isinstance(turn, list) or len(turn) != 3:
                raise RuntimeError("V4 holdout turn schema changed")
            seen_change = seen_change or bool(turn[1])
            if bool(turn[1]) != seen_change:
                raise RuntimeError("V4 holdout pending change cannot disappear")
            if turn[2] and not turn[1]:
                raise RuntimeError("consequential turn must require reconciliation")
