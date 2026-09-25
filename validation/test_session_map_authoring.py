"""Contracts for bounded, source-validated session-map authoring."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from core.memory.session_map.authoring import (
    MAX_AUTHORING_DELTA_CHARACTERS,
    MAX_AUTHORING_DELTA_MESSAGES,
    CanonicalMapMessage,
    GoalProposal,
    PutProposal,
    SessionMapAuthoringRequest,
    SessionMapPatchProposal,
    build_session_map_authoring_prompt,
    compile_patch_proposal,
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


def test_authoring_request_bounds_large_reconciliation_spans() -> None:
    empty = SessionMap.empty(session_id="bounded-authoring", created_at=NOW)
    with pytest.raises(ValueError, match="exceeds .* messages"):
        SessionMapAuthoringRequest(
            current_map=empty,
            delta=tuple(
                CanonicalMapMessage(
                    sequence_index=index,
                    role="user",
                    content="evidence",
                )
                for index in range(MAX_AUTHORING_DELTA_MESSAGES + 1)
            ),
            observed_source_content_revision=1,
        )

    message_count = 16
    oversized_content = "x" * (MAX_AUTHORING_DELTA_CHARACTERS // message_count + 1)
    with pytest.raises(ValueError, match="exceeds .* characters"):
        SessionMapAuthoringRequest(
            current_map=empty,
            delta=tuple(
                CanonicalMapMessage(
                    sequence_index=index,
                    role="user",
                    content=oversized_content,
                )
                for index in range(message_count)
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
    assert payload["current_session_map"]["entries"] == []


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


def test_compact_proposal_derives_envelope_roles_and_bookkeeping() -> None:
    request = _request()
    proposal = SessionMapPatchProposal(
        operations=(
            PutProposal(
                entry=GoalProposal(
                    id="goal_release_note",
                    text="Prepare the release note.",
                    status=GoalStatus.ACTIVE,
                    evidence_sequence_indexes=(0,),
                )
            ),
        )
    )
    patch_set = compile_patch_proposal(request, proposal)
    assert patch_set.expected_revision == 0
    assert patch_set.through_sequence_index == 1
    assert patch_set.observed_source_content_revision == 1
    operation = patch_set.operations[0]
    assert isinstance(operation, AddPatch)
    assert operation.entry.source_refs == (SourceRef(sequence_index=0, role="user"),)
    assert operation.entry.active_from_sequence_index == 0
    assert operation.entry.last_state_change_sequence_index == 0


def test_compact_proposal_exposes_domain_entry_id_constraint() -> None:
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        GoalProposal(
            id="goal-with-hyphens",
            text="Prepare the release note.",
            status=GoalStatus.ACTIVE,
            evidence_sequence_indexes=(0,),
        )


def test_empty_compact_proposal_compiles_to_domain_noop() -> None:
    patch_set = compile_patch_proposal(
        _request(), SessionMapPatchProposal(operations=())
    )
    assert patch_set.operations == (NoopPatch(reason="No durable session-map change"),)


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
