"""Deterministic contracts for the live session-map domain and store."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelRequest, UserPromptPart

import core.chat.chat_store as chat_store_module
from core.chat.chat_store import ChatStore
from core.identity import LOCAL_USER_PRINCIPAL_ID
from core.memory.session_map.models import (
    AddPatch,
    Attention,
    ChangeAttentionPatch,
    GoalEntry,
    GoalStatus,
    MapPatchSet,
    SessionMap,
    SourceRef,
    SupersedePatch,
    UpdatePatch,
    WorkItemEntry,
    WorkItemStatus,
    apply_patch_set,
    render_session_map,
)
from core.memory.session_map.store import (
    SessionMapConflictError,
    SessionMapStore,
)

NOW = datetime(2026, 9, 25, tzinfo=UTC)


def test_patch_application_preserves_identity_and_requires_delta_evidence() -> None:
    initial = SessionMap.empty(session_id="session-1", created_at=NOW)
    added = apply_patch_set(
        initial,
        MapPatchSet(
            expected_revision=0,
            through_sequence_index=3,
            observed_source_content_revision=1,
            operations=(
                AddPatch(
                    entry=GoalEntry(
                        id="goal_1",
                        text="Ship the deterministic session map.",
                        status=GoalStatus.ACTIVE,
                        source_refs=(SourceRef(sequence_index=1, role="user"),),
                    )
                ),
                AddPatch(
                    entry=WorkItemEntry(
                        id="work_1",
                        goal_ids=("goal_1",),
                        text="Implement patch validation.",
                        status=WorkItemStatus.IN_PROGRESS,
                        next_action="Run focused tests.",
                        next_action_owner="assistant",
                        source_refs=(SourceRef(sequence_index=3, role="assistant"),),
                    )
                ),
                ChangeAttentionPatch(
                    active_goal_ids=("goal_1",),
                    active_work_item_id="work_1",
                    evidence_refs=(SourceRef(sequence_index=3, role="assistant"),),
                ),
            ),
        ),
        created_at=NOW,
    )
    assert added.revision == 1
    assert added.attention == Attention(
        active_goal_ids=("goal_1",),
        active_work_item_id="work_1",
        changed_at_sequence_index=3,
    )

    updated = apply_patch_set(
        added,
        MapPatchSet(
            expected_revision=1,
            through_sequence_index=5,
            observed_source_content_revision=2,
            operations=(
                UpdatePatch(
                    entry_id="work_1",
                    changes={"next_action": "Persist the first revision."},
                    evidence_refs=(SourceRef(sequence_index=5, role="user"),),
                ),
            ),
        ),
        created_at=NOW,
    )
    assert updated.work_items[0].text == "Implement patch validation."
    assert updated.work_items[0].next_action == "Persist the first revision."
    assert updated.work_items[0].state_source_refs[-1].sequence_index == 5

    with pytest.raises(ValueError, match="identity-bearing field 'text'"):
        apply_patch_set(
            updated,
            MapPatchSet(
                expected_revision=2,
                through_sequence_index=6,
                observed_source_content_revision=3,
                operations=(
                    UpdatePatch(
                        entry_id="work_1",
                        changes={"text": "Silently rewritten work."},
                        evidence_refs=(SourceRef(sequence_index=6, role="assistant"),),
                    ),
                ),
            ),
            created_at=NOW,
        )

    with pytest.raises(ValueError, match="outside patch delta"):
        apply_patch_set(
            updated,
            MapPatchSet(
                expected_revision=2,
                through_sequence_index=7,
                observed_source_content_revision=3,
                operations=(
                    UpdatePatch(
                        entry_id="work_1",
                        changes={"next_action": "Invalid old evidence."},
                        evidence_refs=(SourceRef(sequence_index=5, role="user"),),
                    ),
                ),
            ),
            created_at=NOW,
        )

    completed = apply_patch_set(
        updated,
        MapPatchSet(
            expected_revision=2,
            through_sequence_index=7,
            observed_source_content_revision=3,
            operations=(
                UpdatePatch(
                    entry_id="work_1",
                    changes={"status": "completed"},
                    evidence_refs=(SourceRef(sequence_index=7, role="assistant"),),
                ),
                ChangeAttentionPatch(
                    active_goal_ids=("goal_1",),
                    evidence_refs=(SourceRef(sequence_index=7, role="assistant"),),
                ),
            ),
        ),
        created_at=NOW,
    )
    with pytest.raises(ValueError, match="illegal work_item.status transition"):
        apply_patch_set(
            completed,
            MapPatchSet(
                expected_revision=3,
                through_sequence_index=8,
                observed_source_content_revision=4,
                operations=(
                    UpdatePatch(
                        entry_id="work_1",
                        changes={"status": "in_progress"},
                        evidence_refs=(SourceRef(sequence_index=8, role="assistant"),),
                    ),
                ),
            ),
            created_at=NOW,
        )


def test_supersession_replaces_working_entry_and_audit_keeps_operation() -> None:
    initial = _map_with_goal()
    next_map = apply_patch_set(
        initial,
        MapPatchSet(
            expected_revision=1,
            through_sequence_index=4,
            observed_source_content_revision=2,
            operations=(
                SupersedePatch(
                    entry_id="goal_1",
                    replacement=GoalEntry(
                        id="goal_2",
                        text="Ship map-backed pruning.",
                        status=GoalStatus.ACTIVE,
                        source_refs=(SourceRef(sequence_index=4, role="user"),),
                    ),
                    evidence_refs=(SourceRef(sequence_index=4, role="user"),),
                ),
                ChangeAttentionPatch(
                    active_goal_ids=("goal_2",),
                    evidence_refs=(SourceRef(sequence_index=4, role="user"),),
                ),
            ),
        ),
        created_at=NOW,
    )
    assert [goal.id for goal in next_map.goals] == ["goal_2"]
    assert "goal_1" not in render_session_map(next_map, max_chars=1000).text


def test_planned_work_can_complete_when_evidence_skips_intermediate_state() -> None:
    current = _map_with_goal().model_copy(
        update={
            "work_items": (
                WorkItemEntry(
                    id="work_planned",
                    goal_ids=("goal_1",),
                    text="Collect source notes.",
                    status=WorkItemStatus.PLANNED,
                    source_refs=(SourceRef(sequence_index=2, role="assistant"),),
                ),
            )
        }
    )
    completed = apply_patch_set(
        current,
        MapPatchSet(
            expected_revision=1,
            through_sequence_index=3,
            observed_source_content_revision=2,
            operations=(
                UpdatePatch(
                    entry_id="work_planned",
                    changes={"status": "completed"},
                    evidence_refs=(SourceRef(sequence_index=3, role="tool"),),
                ),
            ),
        ),
        created_at=NOW,
    )
    assert completed.work_items[0].status == WorkItemStatus.COMPLETED


def test_map_validation_and_rendering_keep_current_handoff() -> None:
    current = _map_with_goal()
    with pytest.raises(ValidationError, match="active_goal_ids"):
        current.model_copy(
            update={
                "attention": Attention(
                    active_goal_ids=("missing",),
                    changed_at_sequence_index=2,
                )
            }
        ).model_validate(
            current.model_copy(
                update={
                    "attention": Attention(
                        active_goal_ids=("missing",),
                        changed_at_sequence_index=2,
                    )
                }
            ).model_dump()
        )

    rendered = render_session_map(current, max_chars=260)
    assert "Ship the deterministic session map" in rendered.text
    assert rendered.included_entry_ids == ("goal_1",)


def test_store_compare_and_swap_pending_recovery_and_cascade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_store_module, "get_persist_model_reasoning_parts", lambda: False
    )
    with tempfile.TemporaryDirectory(prefix="assistantmd-session-map-") as root:
        chat_store = ChatStore(root)
        session_id = "session-store"
        vault_name = "Vault"
        chat_store.ensure_session(
            session_id,
            vault_name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        map_store = SessionMapStore(root)

        with chat_store.transaction() as conn:
            chat_store.add_messages(
                session_id,
                vault_name,
                [ModelRequest(parts=[UserPromptPart(content="Start work")])],
                connection=conn,
            )
            map_store.record_pending(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                through_sequence_index=0,
                token_count=12,
            )

        pending = map_store.get_maintenance_state(session_id, vault_name)
        assert pending is not None
        assert pending.pending_turn_count == 1
        assert pending.pending_token_count == 12
        assert pending.observed_source_content_revision == 1

        attempt = map_store.freeze_attempt(session_id, vault_name)
        assert attempt.frozen_from_sequence_index == 0
        assert attempt.frozen_through_sequence_index == 0

        with chat_store.transaction() as conn:
            chat_store.add_messages(
                session_id,
                vault_name,
                [ModelRequest(parts=[UserPromptPart(content="Arrived during work")])],
                connection=conn,
            )
            map_store.record_pending(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                through_sequence_index=1,
                token_count=7,
            )

        first = _map_with_goal(session_id=session_id, through=0)
        first_operations = (
            AddPatch(entry=first.goals[0]),
            ChangeAttentionPatch(
                active_goal_ids=("goal_1",),
                evidence_refs=(SourceRef(sequence_index=0, role="user"),),
            ),
        )
        map_store.commit_revision(
            session_id=session_id,
            vault_name=vault_name,
            expected_revision=0,
            session_map=first,
            operations=first_operations,
        )
        assert map_store.get_latest_revision(session_id, vault_name) == first
        remaining = map_store.get_maintenance_state(session_id, vault_name)
        assert remaining is not None and remaining.status == "pending"
        assert remaining.pending_turn_count == 1
        assert remaining.pending_token_count == 7

        map_store.freeze_attempt(session_id, vault_name)
        wrong_role_map = first.model_copy(
            update={
                "revision": 2,
                "updated_through_sequence_index": 1,
                "observed_source_content_revision": 2,
                "goals": (
                    first.goals[0].model_copy(
                        update={
                            "source_refs": (
                                SourceRef(sequence_index=0, role="assistant"),
                            )
                        }
                    ),
                ),
            }
        )
        with pytest.raises(ValueError, match="role does not match"):
            map_store.commit_revision(
                session_id=session_id,
                vault_name=vault_name,
                expected_revision=1,
                session_map=wrong_role_map,
                operations=first_operations,
            )
        map_store.fail_attempt(
            session_id,
            vault_name,
            error_type="invalid_source",
            retryable=False,
        )
        failed = map_store.get_maintenance_state(session_id, vault_name)
        assert failed is not None and failed.status == "pending"
        assert failed.last_error == {
            "error_type": "invalid_source",
            "retryable": False,
        }

        with pytest.raises(SessionMapConflictError):
            map_store.commit_revision(
                session_id=session_id,
                vault_name=vault_name,
                expected_revision=0,
                session_map=first.model_copy(update={"revision": 2}),
                operations=(),
            )

        with chat_store.transaction() as conn:
            chat_store.add_messages(
                session_id,
                vault_name,
                [ModelRequest(parts=[UserPromptPart(content="More work")])],
                connection=conn,
            )
            map_store.record_pending(
                conn,
                session_id=session_id,
                vault_name=vault_name,
                through_sequence_index=2,
                token_count=7,
            )
        map_store.freeze_attempt(session_id, vault_name)
        recovered = SessionMapStore(root).recover_interrupted_attempts()
        assert recovered == 1
        state = map_store.get_maintenance_state(session_id, vault_name)
        assert state is not None and state.status == "pending"
        assert state.pending_turn_count == 2

        chat_store.delete_sessions(vault_name, session_id=session_id)
        assert map_store.get_latest_revision(session_id, vault_name) is None
        assert map_store.get_maintenance_state(session_id, vault_name) is None


def test_canonical_range_read_is_bounded_and_raw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_store_module, "get_persist_model_reasoning_parts", lambda: False
    )
    with tempfile.TemporaryDirectory(prefix="assistantmd-session-map-range-") as root:
        store = ChatStore(root)
        store.ensure_session(
            "range-session",
            "Vault",
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            "range-session",
            "Vault",
            [
                ModelRequest(parts=[UserPromptPart(content="zero")]),
                ModelRequest(parts=[UserPromptPart(content="one")]),
                ModelRequest(parts=[UserPromptPart(content="two")]),
            ],
        )
        rows = store.get_stored_messages_range(
            "range-session",
            "Vault",
            after_sequence_index=0,
            through_sequence_index=1,
        )
        assert [row.sequence_index for row in rows] == [1]


def _map_with_goal(*, session_id: str = "session-1", through: int = 2) -> SessionMap:
    return SessionMap(
        session_id=session_id,
        revision=1,
        updated_through_sequence_index=through,
        observed_source_content_revision=1,
        created_at=NOW,
        updated_at=NOW,
        attention=Attention(
            active_goal_ids=("goal_1",),
            changed_at_sequence_index=min(through, 2),
        ),
        goals=(
            GoalEntry(
                id="goal_1",
                text="Ship the deterministic session map.",
                status=GoalStatus.ACTIVE,
                source_refs=(SourceRef(sequence_index=min(through, 1), role="user"),),
            ),
        ),
    )
