"""Live-model probe for sequential typed session-map patch authoring."""

from __future__ import annotations

import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority  # noqa: E402
from core.memory.session_map.authoring import (  # noqa: E402
    SESSION_MAP_AUTHORING_PROMPT_VERSION,
    CanonicalMapMessage,
    SessionMapAuthoringRequest,
    author_session_map_patch,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMap,
    render_session_map,
    session_map_entries,
)
from validation.core.base_scenario import BaseScenario  # noqa: E402
from validation.core.live_session_memory_corpus import (  # noqa: E402
    load_live_session_memory_corpus,
)

AUTHORING_MODEL_ALIAS = "gpt-mini"
CALIBRATION_CASE_IDS = {
    "research_memo_corrections",
    "read_only_migration_diagnosis",
}
HOLDOUT_CASE_IDS = {"goal_switch_and_cancellation"}


class SessionMapAuthoringProbeScenario(BaseScenario):
    """Capture typed authoring behavior without connecting it to chat runtime."""

    async def test_scenario(self) -> None:
        corpus = load_live_session_memory_corpus()
        await self.start_system()
        cases: list[dict[str, Any]] = []
        with use_execution_authority(LOCAL_USER_AUTHORITY):
            for case in corpus["cases"]:
                cases.append(await _run_case(case))

        successful_records = [
            record
            for case in cases
            for record in case["records"]
            if record["status"] == "applied"
        ]
        failed_records = [
            record
            for case in cases
            for record in case["records"]
            if record["status"] != "applied"
        ]
        latencies = [float(record["latency_seconds"]) for record in successful_records]
        summary = {
            "prompt_contract_version": SESSION_MAP_AUTHORING_PROMPT_VERSION,
            "corpus_version": corpus["corpus_version"],
            "model_alias": AUTHORING_MODEL_ALIAS,
            "calibration_case_ids": sorted(CALIBRATION_CASE_IDS),
            "holdout_case_ids": sorted(HOLDOUT_CASE_IDS),
            "patches_expected": 9,
            "patches_applied": len(successful_records),
            "patches_failed": len(failed_records),
            "source_reference_precision": (
                1.0 if len(successful_records) == 9 else None
            ),
            "unchanged_entry_mutation_rate": (
                0.0 if len(successful_records) == 9 else None
            ),
            "total_input_tokens": sum(
                int(record["input_tokens"]) for record in successful_records
            ),
            "total_output_tokens": sum(
                int(record["output_tokens"]) for record in successful_records
            ),
            "median_latency_seconds": (
                statistics.median(latencies) if latencies else None
            ),
            "semantic_review": {
                "status": "pending_manual_review",
                "required_entry_recall": None,
                "unsupported_entry_rate": None,
                "stale_active_rate": None,
                "unsupported_state_promotions": None,
            },
            "cases": cases,
        }
        (self.artifacts_dir / "session_map_authoring.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        self.soft_assert_equal(
            len(successful_records),
            9,
            "Every first-pass patch must validate and apply without repair",
        )
        self.soft_assert_equal(
            len(failed_records),
            0,
            "No authored patch may fail its typed or source boundary",
        )
        self.teardown_scenario()
        self.assert_no_failures()


async def _run_case(case: dict[str, Any]) -> dict[str, Any]:
    current = SessionMap.empty(
        session_id=f"authoring-probe-{case['id']}",
        created_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    records: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(case["classifier_batches"]):
        delta = tuple(
            _message_from_fixture(message)
            for message in case["messages"]
            if batch["from_sequence_index"]
            <= message["sequence_index"]
            <= batch["through_sequence_index"]
        )
        request = SessionMapAuthoringRequest(
            current_map=current,
            delta=delta,
            observed_source_content_revision=batch_index + 1,
        )
        try:
            result = await author_session_map_patch(
                model_alias=AUTHORING_MODEL_ALIAS,
                request=request,
                created_at=datetime(2026, 9, 25, batch_index + 1, tzinfo=UTC),
            )
        except Exception as exc:
            records.append(
                {
                    "batch_index": batch_index,
                    "from_sequence_index": batch["from_sequence_index"],
                    "through_sequence_index": batch["through_sequence_index"],
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )
            break

        current = result.session_map
        rendered = render_session_map(current, max_chars=20_000)
        records.append(
            {
                "batch_index": batch_index,
                "from_sequence_index": batch["from_sequence_index"],
                "through_sequence_index": batch["through_sequence_index"],
                "status": "applied",
                "patch_set": result.patch_set.model_dump(mode="json"),
                "session_map": current.model_dump(mode="json"),
                "entry_count": len(session_map_entries(current)),
                "rendered_characters": len(rendered.text),
                "requested_model_alias": result.requested_model_alias,
                "resolved_model_name": result.resolved_model_name,
                "provider_name": result.provider_name,
                "latency_seconds": result.latency_seconds,
                "requests": result.requests,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            }
        )
    return {
        "case_id": case["id"],
        "split": ("calibration" if case["id"] in CALIBRATION_CASE_IDS else "holdout"),
        "expected_final_map": case["expected_final_map"],
        "records": records,
    }


def _message_from_fixture(message: dict[str, Any]) -> CanonicalMapMessage:
    kind = message["kind"]
    role = "assistant" if kind == "assistant_tool_call" else kind
    content = message["content"]
    if kind in {"assistant_tool_call", "tool"}:
        content = (
            f"{content} [tool={message['tool_name']}; "
            f"tool_call_id={message['tool_call_id']}]"
        )
    return CanonicalMapMessage(
        sequence_index=message["sequence_index"],
        role=role,
        content=content,
    )
