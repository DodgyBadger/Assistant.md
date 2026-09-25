"""Live-model baseline for repeated recovery-card compaction.

This opt-in scenario replays the frozen live-session-memory corpus through the
current compaction implementation. Assertions cover only mechanical contracts;
generated prose and diagnostic lexical indicators are retained for review.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from core.tools.utils import estimate_token_count
from validation.core.base_scenario import BaseScenario
from validation.core.live_session_memory_corpus import (
    corpus_message_to_model_message,
    load_live_session_memory_corpus,
)

BASELINE_MODEL_ALIAS = "gpt-mini"
BASELINE_API_KEY_ENV = "ASSISTANTMD_VALIDATION_OPENAI_API_KEY"
BASELINE_FOCUS = (
    "Preserve only the current objective, exact work in progress, active constraints, "
    "unresolved blockers or questions, volatile artifact state, and the next action."
)


class RepeatedCompactionLiveProbeScenario(BaseScenario):
    """Capture current recovery-card quality over the frozen Slice 0 corpus."""

    async def test_scenario(self):
        corpus = load_live_session_memory_corpus()
        vault = self.create_vault("RepeatedCompactionLiveProbeVault")

        await self.start_system()

        api_key = os.environ.get(BASELINE_API_KEY_ENV, "").strip()
        assert (
            api_key
        ), f"Set {BASELINE_API_KEY_ENV} to run this opt-in live-service baseline"
        secret_response = self.call_api(
            "/api/system/secrets",
            method="PUT",
            data={"name": "OPENAI_API_KEY", "value": api_key},
        )
        assert (
            secret_response.status_code == 200
        ), "Live compaction probe should seed its isolated encrypted secret store"
        model_response = self.call_api(
            "/api/system/settings/general/default_model",
            method="PUT",
            data={"value": BASELINE_MODEL_ALIAS},
        )
        assert (
            model_response.status_code == 200
        ), "Live compaction probe should select its declared baseline model"

        import core.chat.compaction as compaction
        from core.chat.chat_store import ChatStore
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()
        store = ChatStore(system_root=str(runtime.config.system_root))
        prompt_captures: list[str] = []
        original_keep_recent = compaction.get_compaction_keep_recent
        original_generate_summary = compaction._generate_compaction_summary

        async def _capturing_generate_summary(
            *, older_messages, recent_messages, focus
        ):
            prompt_captures.append(
                compaction._build_summary_prompt(
                    older_messages=older_messages,
                    recent_messages=recent_messages,
                    focus=focus,
                )
            )
            return await original_generate_summary(
                older_messages=older_messages,
                recent_messages=recent_messages,
                focus=focus,
            )

        baseline_cases: list[dict[str, Any]] = []
        compaction.get_compaction_keep_recent = lambda: 1
        compaction._generate_compaction_summary = _capturing_generate_summary
        try:
            for case in corpus["cases"]:
                baseline_cases.append(
                    await _run_case(
                        case=case,
                        vault_name=vault.name,
                        vault_path=str(vault),
                        store=store,
                        compaction=compaction,
                        prompt_captures=prompt_captures,
                        owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
                    )
                )
        finally:
            compaction.get_compaction_keep_recent = original_keep_recent
            compaction._generate_compaction_summary = original_generate_summary

        baseline = {
            "corpus_version": corpus["corpus_version"],
            "rubric_version": corpus["rubric_version"],
            "model_alias": BASELINE_MODEL_ALIAS,
            "compaction_prompt_contract": compaction.CHAT_HISTORY_COMPACTION_PROMPT_VERSION,
            "indicator_kind": "literal_phrase_diagnostic_only",
            "cases": baseline_cases,
        }
        (self.artifacts_dir / "repeated_compaction_baseline.json").write_text(
            json.dumps(baseline, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.artifacts_dir / "repeated_compaction_cards.md").write_text(
            _render_cards(baseline_cases),
            encoding="utf-8",
        )

        assert len(baseline_cases) == len(corpus["cases"])
        assert all(len(case["rounds"]) >= 3 for case in baseline_cases)
        assert all(
            round_result["status"] == "completed"
            for case in baseline_cases
            for round_result in case["rounds"]
        ), "Every live baseline compaction should complete"
        assert all(
            case["rounds"][index]["prompt_contains_prior_recovery_card"]
            for case in baseline_cases
            for index in range(1, len(case["rounds"]))
        ), "Current repeated compaction should expose the prior card to later rounds"

        await self.stop_system()
        self.teardown_scenario()


async def _run_case(
    *,
    case: dict[str, Any],
    vault_name: str,
    vault_path: str,
    store: Any,
    compaction: Any,
    prompt_captures: list[str],
    owner_principal_id: str,
) -> dict[str, Any]:
    session_id = f"repeated_compaction_baseline_{case['id']}"
    store.ensure_session(
        session_id,
        vault_name,
        owner_principal_id=owner_principal_id,
    )
    next_message_offset = 0
    rounds: list[dict[str, Any]] = []

    for round_index, checkpoint in enumerate(case["compaction_checkpoints"], start=1):
        through = checkpoint["after_sequence_index"]
        pending_messages = case["messages"][next_message_offset : through + 1]
        store.add_messages(
            session_id,
            vault_name,
            [corpus_message_to_model_message(message) for message in pending_messages],
        )
        next_message_offset = through + 1

        prompt_index = len(prompt_captures)
        result = await compaction.compact_chat_history(
            session_id=session_id,
            vault_name=vault_name,
            vault_path=vault_path,
            focus=BASELINE_FOCUS,
            store=store,
        )
        assert (
            len(prompt_captures) == prompt_index + 1
        ), "Each compaction should produce exactly one captured authoring prompt"
        prompt = prompt_captures[prompt_index]
        effective_messages = store.get_stored_messages(session_id, vault_name)
        assert effective_messages[0].role == "system"
        card = effective_messages[0].content_text
        assert "AssistantMD compacted chat history" in card

        indicators = _literal_card_indicators(
            card,
            checkpoint["expected_recovery_card"],
        )
        rounds.append(
            {
                "round": round_index,
                "through_sequence_index": through,
                "status": result.status,
                "compaction_id": result.compaction_id,
                "messages_before": result.messages_before,
                "messages_after": result.messages_after,
                "prompt_characters": len(prompt),
                "prompt_estimated_tokens": estimate_token_count(prompt),
                "card_characters": len(card),
                "card_estimated_tokens": estimate_token_count(card),
                "prompt_contains_prior_recovery_card": (
                    "AssistantMD compacted chat history" in prompt
                ),
                "expected_recovery_card": checkpoint["expected_recovery_card"],
                "literal_indicators": indicators,
                "semantic_review": {
                    "unsupported_claim_rate": None,
                    "active_goal_accuracy": None,
                    "current_focus_accuracy": None,
                    "blocker_accuracy": None,
                    "next_action_accuracy": None,
                    "volatile_artifact_accuracy": None,
                },
                "card": card,
            }
        )

    assert store.get_message_count(session_id, vault_name, mode="raw") == len(
        case["messages"]
    ), "Live baseline compaction must preserve every canonical corpus message"

    first = rounds[0]["literal_indicators"]
    last = rounds[-1]["literal_indicators"]
    return {
        "case_id": case["id"],
        "description": case["description"],
        "rounds": rounds,
        "literal_repeated_card_drift": {
            "continuation_coverage_delta": (
                last["continuation_coverage"] - first["continuation_coverage"]
            ),
            "historical_leakage_rate_delta": (
                last["historical_leakage_rate"] - first["historical_leakage_rate"]
            ),
        },
    }


def _literal_card_indicators(
    card: str,
    expectations: dict[str, list[str]],
) -> dict[str, Any]:
    normalized = " ".join(card.casefold().split())
    required = {
        concept: " ".join(concept.casefold().split()) in normalized
        for concept in expectations["required_concepts"]
    }
    forbidden = {
        concept: " ".join(concept.casefold().split()) in normalized
        for concept in expectations["forbidden_concepts"]
    }
    return {
        "required_literal_matches": required,
        "forbidden_literal_matches": forbidden,
        "continuation_coverage": sum(required.values()) / len(required),
        "historical_leakage_rate": sum(forbidden.values()) / len(forbidden),
    }


def _render_cards(cases: list[dict[str, Any]]) -> str:
    sections = ["# Repeated Compaction Baseline Cards"]
    for case in cases:
        sections.append(f"## {case['case_id']}")
        for round_result in case["rounds"]:
            sections.extend(
                [
                    f"### Round {round_result['round']}",
                    round_result["card"],
                ]
            )
    return "\n\n".join(sections).rstrip() + "\n"
