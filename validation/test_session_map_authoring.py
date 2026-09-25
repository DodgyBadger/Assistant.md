"""Contracts for bounded, source-validated session-map authoring."""

import json
from datetime import UTC, datetime

import pytest

from core.memory.session_map.authoring import (
    CanonicalMapMessage,
    SessionMapAuthoringRequest,
    build_session_map_authoring_prompt,
    validate_authored_patch,
)
from core.memory.session_map.models import (
    AddPatch,
    GoalEntry,
    GoalStatus,
    MapPatchSet,
    NoopPatch,
    SessionMap,
    SessionMapError,
    SourceRef,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _request() -> SessionMapAuthoringRequest:
    return SessionMapAuthoringRequest(
        current_map=SessionMap.empty(session_id="authoring-test", created_at=NOW),
        delta=(
            CanonicalMapMessage(
                sequence_index=0,
                role="user",
                content="Prepare the release note.",
            ),
            CanonicalMapMessage(
                sequence_index=1,
                role="assistant",
                content="I will draft it next.",
            ),
        ),
        observed_source_content_revision=1,
    )


def test_authoring_request_requires_exact_contiguous_delta() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        SessionMapAuthoringRequest(
            current_map=SessionMap.empty(session_id="gap", created_at=NOW),
            delta=(
                CanonicalMapMessage(sequence_index=0, role="user", content="Start"),
                CanonicalMapMessage(sequence_index=2, role="assistant", content="Done"),
            ),
            observed_source_content_revision=1,
        )


def test_authoring_prompt_carries_machine_readable_controls_and_sources() -> None:
    payload = json.loads(build_session_map_authoring_prompt(_request()))
    assert payload["controls"] == {
        "expected_revision": 0,
        "through_sequence_index": 1,
        "observed_source_content_revision": 1,
    }
    assert payload["canonical_delta"][0] == {
        "sequence_index": 0,
        "role": "user",
        "content": "Prepare the release note.",
    }
    assert payload["current_session_map"]["goals"] == []


def test_validated_authoring_patch_applies_without_rewriting_controls() -> None:
    request = _request()
    patch_set = MapPatchSet(
        expected_revision=0,
        through_sequence_index=1,
        observed_source_content_revision=1,
        operations=(
            AddPatch(
                entry=GoalEntry(
                    id="goal_release_note",
                    text="Prepare the release note.",
                    status=GoalStatus.ACTIVE,
                    source_refs=(SourceRef(sequence_index=0, role="user"),),
                    active_from_sequence_index=0,
                )
            ),
        ),
    )
    result = validate_authored_patch(request, patch_set, created_at=NOW)
    assert result.revision == 1
    assert result.updated_through_sequence_index == 1
    assert result.goals[0].id == "goal_release_note"


def test_authoring_rejects_wrong_source_role_and_envelope_values() -> None:
    request = _request()
    wrong_role = MapPatchSet(
        expected_revision=0,
        through_sequence_index=1,
        observed_source_content_revision=1,
        operations=(
            AddPatch(
                entry=GoalEntry(
                    id="goal_release_note",
                    text="Prepare the release note.",
                    status=GoalStatus.ACTIVE,
                    source_refs=(SourceRef(sequence_index=0, role="assistant"),),
                )
            ),
        ),
    )
    with pytest.raises(SessionMapError, match="expected 'user'"):
        validate_authored_patch(request, wrong_role, created_at=NOW)

    wrong_revision = MapPatchSet(
        expected_revision=1,
        through_sequence_index=1,
        observed_source_content_revision=1,
        operations=(NoopPatch(reason="No durable change"),),
    )
    with pytest.raises(SessionMapError, match="unexpected map revision"):
        validate_authored_patch(request, wrong_revision, created_at=NOW)
