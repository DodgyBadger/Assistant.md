"""Canonical session-map checkpoints preserve revisions and raw history."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_journey_inputs import (
    conversation,
    range_evidence,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_map_checkpoints_preserve_provenance_and_reject_stale_commits(
    app: Application,
) -> None:
    vault = app.vault("SessionMapCheckpointVault")
    await app.start()
    from core.memory.session_map.checkpoints import (
        build_session_map_context_message,
        commit_session_map_context_checkpoint,
        load_session_map_checkpoint,
    )
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapEntry,
        SessionMapTrajectory,
        SourceRange,
    )
    from core.runtime.state import get_runtime_context
    from core.utils.messages import extract_role_and_text

    store = get_runtime_context().chat_store
    session = "session-map-checkpoint"
    raw = conversation(
        [
            ("Review the proposal.", "I will organize the issues."),
            ("The City owns the land.", "That ownership needs legal clarification."),
            ("Draft questions for counsel.", "I drafted the questions."),
        ]
    )
    seed_session(store, session, vault.name, raw)
    revision, evidence = range_evidence(store, session, vault.name, 0, 1)
    stale_revision, stale_evidence = range_evidence(store, session, vault.name, 2, 3)
    assert stale_revision == revision
    first = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="The session began by organizing a legal review of the proposal.",
            sources=(SourceRange(start=0, end=1),),
        ),
        entries=(
            SessionMapEntry(
                id="proposal_legal_review",
                kind="goal",
                state="active",
                basis="user_established",
                text="Review the redevelopment proposal.",
                sources=(SourceRange(start=0, end=1),),
            ),
        ),
    )
    result = commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=first,
        previous_map=SessionMapDraft(),
        new_evidence=evidence,
        expected_history_revision=revision,
        message_count_before=len(raw),
        source="validation",
        authoring_task_id="task-first-map",
        authoring_prompt_version="eviction-map-v8",
        author_model_alias="gpt-mini",
        author_thinking="low",
        checkpoint_id="first-map-checkpoint",
    )
    assert (
        result.checkpoint.checkpoint_kind,
        result.checkpoint.last_message_sequence_index,
    ) == ("session_map", 1)
    assert load_session_map_checkpoint(result.checkpoint) == first
    metadata = json.loads(result.checkpoint.metadata_json or "{}")
    assert {
        key: metadata[key]
        for key in (
            "map_schema_version",
            "authoring_prompt_version",
            "context_prompt_version",
            "author_model_alias",
            "author_thinking",
            "source_history_revision",
            "evidence_source_start_sequence_index",
            "evidence_source_end_sequence_index",
            "map_observed_through_sequence_index",
        )
    } == {
        "map_schema_version": 3,
        "authoring_prompt_version": "eviction-map-v8",
        "context_prompt_version": "session-map-context-v2",
        "author_model_alias": "gpt-mini",
        "author_thinking": "low",
        "source_history_revision": revision,
        "evidence_source_start_sequence_index": 0,
        "evidence_source_end_sequence_index": 1,
        "map_observed_through_sequence_index": 1,
    }
    assert store.get_history(session, vault.name, mode="raw") == raw
    effective = store.get_history(session, vault.name) or []
    assert len(effective) == 5 and effective[1:] == raw[2:]
    assert extract_role_and_text(effective[0]) == extract_role_and_text(
        build_session_map_context_message(first)
    )
    appended = conversation([("Later question.", "Later answer.")])
    store.add_messages(session, vault.name, appended)
    raw.extend(appended)
    effective = store.get_history(session, vault.name) or []
    with pytest.raises(ValueError, match="history revision changed"):
        commit_session_map_context_checkpoint(
            store=store,
            session_id=session,
            vault_name=vault.name,
            draft=first,
            previous_map=first,
            new_evidence=stale_evidence,
            expected_history_revision=stale_revision,
            message_count_before=len(effective),
            source="validation",
            checkpoint_id="stale-map-checkpoint",
        )
    assert (
        len(
            store.list_context_checkpoints(
                session, vault.name, checkpoint_kind="session_map"
            )
        )
        == 1
    )
    assert store.get_history(session, vault.name) == effective
    revision2, evidence2 = range_evidence(store, session, vault.name, 2, 3)
    second = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="The proposal review narrowed to the legal implications of City land ownership.",
            sources=(SourceRange(start=0, end=1), SourceRange(start=2, end=3)),
        ),
        entries=(
            first.entries[0],
            SessionMapEntry(
                id="city_land_authority",
                kind="constraint",
                state="active",
                basis="mixed",
                text="City land ownership requires legal clarification.",
                sources=(SourceRange(start=2, end=3),),
            ),
        ),
    )
    result2 = commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=second,
        previous_map=first,
        new_evidence=evidence2,
        expected_history_revision=revision2,
        message_count_before=len(effective),
        source="validation",
        checkpoint_id="second-map-checkpoint",
    )
    effective2 = store.get_history(session, vault.name) or []
    assert effective2[1:] == raw[4:] and extract_role_and_text(
        effective2[0]
    ) == extract_role_and_text(build_session_map_context_message(second))
    checkpoints = store.list_context_checkpoints(
        session, vault.name, checkpoint_kind="session_map"
    )
    assert [item.checkpoint_id for item in checkpoints] == [
        "first-map-checkpoint",
        "second-map-checkpoint",
    ]
    assert [load_session_map_checkpoint(item) for item in checkpoints] == [
        first,
        second,
    ]
    assert (
        result2.checkpoint.last_message_sequence_index == 3
        and store.get_history(session, vault.name, mode="raw") == raw
    )
    listed = app.request("/api/chat/sessions", params={"vault_name": vault.name})
    assert (
        listed.status_code == 200
        and next(item for item in listed.json() if item["session_id"] == session)[
            "has_session_map"
        ]
        is True
    )
    current = app.request(
        f"/api/chat/sessions/{session}/map", params={"vault_name": vault.name}
    )
    assert (
        current.status_code == 200
        and current.json()["selected_checkpoint_id"] == "second-map-checkpoint"
    )
    assert [item["checkpoint_id"] for item in current.json()["revisions"]] == [
        "first-map-checkpoint",
        "second-map-checkpoint",
    ]
