"""Contracts for typed session-map change-detection questions."""

import tempfile
from pathlib import Path

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="assistantmd-map-change-")
_TEST_ROOT_PATH = Path(_TEST_ROOT.name)
set_bootstrap_roots(_TEST_ROOT_PATH / "data", _TEST_ROOT_PATH / "system")

from core.memory.session_map.change_detection import (  # noqa: E402
    SESSION_MAP_ADEQUACY_FIELDS,
    SESSION_MAP_CUMULATIVE_CHANGE_FIELDS,
    SESSION_MAP_DIMENSIONS,
    SessionDeltaSignals,
    SessionMapAdequacySignals,
    SessionMapCumulativeChangeSignals,
    SessionReconciliationSignal,
    build_cumulative_session_map_adequacy_request,
    build_cumulative_session_map_change_request,
    build_session_delta_request,
    build_session_reconciliation_request,
)


def test_signal_schema_covers_each_map_dimension_with_independent_questions() -> None:
    schema = SessionDeltaSignals.model_json_schema()
    assert tuple(schema["properties"]) == SESSION_MAP_DIMENSIONS
    for dimension in SESSION_MAP_DIMENSIONS:
        field_schema = schema["properties"][dimension]
        assert field_schema["minimum"] == 0
        assert field_schema["maximum"] == 1
        assert field_schema["description"].endswith("?")


def test_change_request_places_only_canonical_delta_in_state() -> None:
    request = build_session_delta_request("  User: cancel the old goal.  ")
    assert request.state == "User: cancel the old goal."
    assert request.output_type is SessionDeltaSignals
    assert "failed/uncommitted attempt" in request.instructions


def test_reconciliation_request_compares_map_with_delta_using_one_question() -> None:
    request = build_session_reconciliation_request(
        "  goal:g1 active | constraint:c1 read-only  ",
        "  assistant: Understood; I will keep this read-only.  ",
    )
    assert request.output_type is SessionReconciliationSignal
    assert tuple(request.output_type.model_json_schema()["properties"]) == (
        "reconciliation_needed",
    )
    assert request.state == (
        "<current_session_map>\n"
        "goal:g1 active | constraint:c1 read-only\n"
        "</current_session_map>\n"
        "<canonical_delta>\n"
        "assistant: Understood; I will keep this read-only.\n"
        "</canonical_delta>"
    )
    assert "already represented" in request.instructions


def test_reconciliation_request_rejects_missing_inputs() -> None:
    for current_map, delta in (("", "new fact"), ("existing map", "  ")):
        try:
            build_session_reconciliation_request(current_map, delta)
        except ValueError:
            continue
        raise AssertionError("empty reconciliation input must fail fast")


def test_cumulative_adequacy_schema_asks_one_stability_question_per_field() -> None:
    schema = SessionMapAdequacySignals.model_json_schema()
    assert tuple(schema["properties"]) == SESSION_MAP_ADEQUACY_FIELDS
    for field_name in SESSION_MAP_ADEQUACY_FIELDS:
        field_schema = schema["properties"][field_name]
        assert field_schema["minimum"] == 0
        assert field_schema["maximum"] == 1
        assert field_schema["description"].endswith("?")


def test_cumulative_adequacy_request_preserves_the_complete_pending_prefix() -> None:
    request = build_cumulative_session_map_adequacy_request(
        "  goal:g1 active | constraint:c1 read-only  ",
        "  user: Keep it read-only.\nassistant: Understood.\nuser: Add a test.  ",
    )
    assert request.output_type is SessionMapAdequacySignals
    assert request.state == (
        "<accepted_session_map>\n"
        "goal:g1 active | constraint:c1 read-only\n"
        "</accepted_session_map>\n"
        "<cumulative_canonical_delta>\n"
        "user: Keep it read-only.\nassistant: Understood.\nuser: Add a test.\n"
        "</cumulative_canonical_delta>"
    )
    assert "every canonical message accumulated" in request.instructions
    assert "yes means the named field remains adequate" in request.instructions


def test_cumulative_adequacy_request_rejects_missing_inputs() -> None:
    for current_map, delta in (("", "new fact"), ("existing map", "  ")):
        try:
            build_cumulative_session_map_adequacy_request(current_map, delta)
        except ValueError:
            continue
        raise AssertionError("empty cumulative adequacy input must fail fast")


def test_cumulative_change_schema_uses_direct_reconciliation_polarity() -> None:
    schema = SessionMapCumulativeChangeSignals.model_json_schema()
    assert tuple(schema["properties"]) == SESSION_MAP_CUMULATIVE_CHANGE_FIELDS
    for field_name in SESSION_MAP_CUMULATIVE_CHANGE_FIELDS:
        field_schema = schema["properties"][field_name]
        assert field_schema["minimum"] == 0
        assert field_schema["maximum"] == 1
        assert field_schema["description"].endswith("?")

    request = build_cumulative_session_map_change_request(
        "goal:g1 active", "user: Change the active goal."
    )
    assert request.output_type is SessionMapCumulativeChangeSignals
    assert "yes means reconciliation is required" in request.instructions
    assert "<cumulative_canonical_delta>" in request.state
