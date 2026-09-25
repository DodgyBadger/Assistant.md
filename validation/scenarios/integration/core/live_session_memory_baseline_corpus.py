"""Validate the replayable baseline corpus for live session-memory experiments."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario
from validation.core.live_session_memory_corpus import (
    corpus_message_to_model_message,
    load_live_session_memory_corpus,
)

EXPECTED_DIMENSIONS = {
    "attention",
    "goals",
    "work_items",
    "decisions",
    "constraints",
    "commitments",
    "open_questions",
    "artifacts",
    "observations",
}
REQUIRED_PHENOMENA = {
    "artifact",
    "constraint",
    "correction",
    "decision",
    "failed_turn",
    "goal",
    "goal_change",
    "open_question",
    "tool_observation",
}


class LiveSessionMemoryBaselineCorpusScenario(BaseScenario):
    """Validate corpus labels and replay canonical messages without a model."""

    async def test_scenario(self):
        corpus = load_live_session_memory_corpus()
        _validate_corpus(corpus)

        vault = self.create_vault("LiveSessionMemoryBaselineCorpusVault")
        await self.start_system()

        from core.chat.chat_store import ChatStore
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()
        store = ChatStore(system_root=str(runtime.config.system_root))
        replay_manifest: list[dict[str, Any]] = []

        for case in corpus["cases"]:
            session_id = f"memory_corpus_{case['id']}"
            store.ensure_session(
                session_id,
                vault.name,
                owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
            )
            messages = [
                corpus_message_to_model_message(item) for item in case["messages"]
            ]
            store.add_messages(session_id, vault.name, messages)

            stored = store.get_stored_messages(
                session_id,
                vault.name,
                mode="raw",
            )
            assert len(stored) == len(
                messages
            ), f"Corpus case {case['id']} should replay every canonical message"
            assert [item.sequence_index for item in stored] == list(
                range(len(messages))
            ), f"Corpus case {case['id']} should preserve canonical sequence identity"
            assert len(store.get_history(session_id, vault.name) or []) == len(
                messages
            ), f"Corpus case {case['id']} should round-trip as provider-native history"

            replay_manifest.append(
                {
                    "case_id": case["id"],
                    "canonical_message_count": len(messages),
                    "failed_attempt_count": len(case["failed_attempts"]),
                    "compaction_checkpoint_count": len(case["compaction_checkpoints"]),
                    "expected_map_entry_count": len(
                        case["expected_final_map"]["entries"]
                    ),
                }
            )

        (self.artifacts_dir / "corpus_replay_manifest.json").write_text(
            json.dumps(
                {
                    "corpus_version": corpus["corpus_version"],
                    "rubric_version": corpus["rubric_version"],
                    "cases": replay_manifest,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        await self.stop_system()
        self.teardown_scenario()


def _validate_corpus(corpus: dict[str, Any]) -> None:
    assert corpus["corpus_version"] == 1, "Corpus format changes must be versioned"
    assert corpus["rubric_version"], "Corpus must identify its scoring contract"
    assert (
        set(corpus["dimensions"]) == EXPECTED_DIMENSIONS
    ), "Corpus must label every proposed session-map dimension"
    assert (
        len(corpus["cases"]) >= 3
    ), "Baseline corpus should contain several distinct session shapes"

    case_ids = [case["id"] for case in corpus["cases"]]
    assert len(case_ids) == len(set(case_ids)), "Corpus case IDs must be unique"
    represented_phenomena = {
        phenomenon for case in corpus["cases"] for phenomenon in case["phenomena"]
    }
    assert (
        REQUIRED_PHENOMENA <= represented_phenomena
    ), "Corpus must cover the predeclared continuity and failure phenomena"

    for case in corpus["cases"]:
        _validate_case(case)


def _validate_case(case: dict[str, Any]) -> None:
    messages = case["messages"]
    sequence_indexes = [message["sequence_index"] for message in messages]
    assert sequence_indexes == list(
        range(len(messages))
    ), f"Corpus case {case['id']} must use contiguous canonical sequence indexes"
    _validate_tool_pairs(case)

    for failed_attempt in case["failed_attempts"]:
        assert (
            failed_attempt["committed"] is False
        ), f"Failed attempt in {case['id']} must not masquerade as canonical evidence"
        assert (
            failed_attempt["after_sequence_index"] in sequence_indexes
        ), f"Failed attempt in {case['id']} must be anchored to canonical progress"

    batches = case["classifier_batches"]
    assert (
        batches[0]["from_sequence_index"] == 0
    ), f"Classifier labels for {case['id']} must begin with the first message"
    for prior, current in zip(batches, batches[1:], strict=False):
        assert (
            current["from_sequence_index"] == prior["through_sequence_index"] + 1
        ), f"Classifier ranges for {case['id']} must be contiguous"
    assert (
        batches[-1]["through_sequence_index"] == sequence_indexes[-1]
    ), f"Classifier labels for {case['id']} must cover the complete fixture"
    for batch in batches:
        assert batch["from_sequence_index"] <= batch["through_sequence_index"]
        assert set(batch["changed_dimensions"]) <= EXPECTED_DIMENSIONS
        assert batch[
            "changed_dimensions"
        ], f"Classifier batch in {case['id']} must have an explicit expected result"

    checkpoints = case["compaction_checkpoints"]
    assert (
        len(checkpoints) >= 3
    ), f"Corpus case {case['id']} must exercise at least three compactions"
    checkpoint_indexes = [item["after_sequence_index"] for item in checkpoints]
    assert checkpoint_indexes == sorted(
        set(checkpoint_indexes)
    ), f"Compaction checkpoints for {case['id']} must be unique and ordered"
    assert set(checkpoint_indexes) <= set(
        sequence_indexes
    ), f"Compaction checkpoints for {case['id']} must reference canonical messages"
    for checkpoint in checkpoints:
        expected = checkpoint["expected_recovery_card"]
        assert expected[
            "required_concepts"
        ], f"Checkpoint in {case['id']} must define immediate continuation needs"
        assert expected[
            "forbidden_concepts"
        ], f"Checkpoint in {case['id']} must define stale or historical leakage"

    expected_map = case["expected_final_map"]
    entries = expected_map["entries"]
    entry_ids = [entry["id"] for entry in entries]
    assert len(entry_ids) == len(
        set(entry_ids)
    ), f"Expected map entry IDs for {case['id']} must be stable and unique"
    entries_by_id = {entry["id"]: entry for entry in entries}
    for entry in entries:
        assert entry["dimension"] in EXPECTED_DIMENSIONS - {"attention"}
        assert entry["meaning"].strip()
        assert entry[
            "source_sequence_indexes"
        ], f"Expected map entry {entry['id']} must retain canonical provenance"
        assert set(entry["source_sequence_indexes"]) <= set(
            sequence_indexes
        ), f"Expected map entry {entry['id']} has a non-canonical source reference"

    attention = expected_map["attention"]
    for goal_id in attention["active_goal_ids"]:
        assert entries_by_id[goal_id]["dimension"] == "goals"
        assert entries_by_id[goal_id]["status"] == "active"
    active_work_item_id = attention["active_work_item_id"]
    if active_work_item_id is not None:
        assert entries_by_id[active_work_item_id]["dimension"] == "work_items"
        assert entries_by_id[active_work_item_id]["status"] == "in_progress"


def _validate_tool_pairs(case: dict[str, Any]) -> None:
    calls = {
        message["tool_call_id"]: message
        for message in case["messages"]
        if message["kind"] == "assistant_tool_call"
    }
    results = {
        message["tool_call_id"]: message
        for message in case["messages"]
        if message["kind"] == "tool"
    }
    assert (
        calls.keys() == results.keys()
    ), f"Tool calls and results in {case['id']} must form complete canonical pairs"
    for tool_call_id, call in calls.items():
        assert (
            call["tool_name"] == results[tool_call_id]["tool_name"]
        ), f"Tool pair {tool_call_id} in {case['id']} must keep one tool identity"
        assert (
            call["sequence_index"] < results[tool_call_id]["sequence_index"]
        ), f"Tool result {tool_call_id} in {case['id']} must follow its call"
