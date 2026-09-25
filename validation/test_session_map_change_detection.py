"""Contracts for typed session-map change-detection questions."""

import tempfile
from pathlib import Path

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="assistantmd-map-change-")
_TEST_ROOT_PATH = Path(_TEST_ROOT.name)
set_bootstrap_roots(_TEST_ROOT_PATH / "data", _TEST_ROOT_PATH / "system")

from core.memory.session_map.change_detection import (  # noqa: E402
    SESSION_MAP_DIMENSIONS,
    SessionDeltaSignals,
    build_session_delta_request,
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
