"""Mapped checkpoint boundaries preserve canonical tool turns and provenance."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.checkpoint_boundary_inputs import seed_tool_timeline
from validation.scenarios.support.session_map_journey_inputs import (
    conversation,
    range_evidence,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_checkpoint_tool_timeline_preserves_turn_paging_and_scoped_detail(
    app: Application,
) -> None:
    vault = app.vault("MappedToolTimelineVault")
    await app.start()
    from core.memory.session_map.checkpoints import (
        commit_session_map_context_checkpoint,
    )
    from core.memory.session_map.models import SessionMapDraft
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session, retry = seed_tool_timeline(store, vault.name)
    retry_rows, has_older = store.get_canonical_display_rows_before(
        retry,
        vault.name,
        through_sequence_index=3,
        before_sequence_index=None,
        limit=10,
    )
    assert retry_rows == [(0, 0, "message"), (1, 3, "message")] and has_older is False
    revision, evidence = range_evidence(store, session, vault.name, 0, 3)
    commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=SessionMapDraft(),
        previous_map=SessionMapDraft(),
        new_evidence=evidence,
        expected_history_revision=revision,
        message_count_before=6,
        source="validation",
        checkpoint_id="tool-transcript-checkpoint",
    )
    timeline = app.request(
        f"/api/chat/sessions/{session}/timeline",
        params={"vault_name": vault.name, "page_size": 100},
    )
    assert timeline.status_code == 200
    messages = timeline.json()["messages"]
    assert [(item["sequence_index"], item["role"]) for item in messages] == [
        (0, "user"),
        (1, "assistant"),
        (4, "user"),
        (5, "assistant"),
    ]
    assert all(
        item["content"].strip() for item in messages if item["role"] == "assistant"
    )
    assert (
        messages[1]["through_sequence_index"],
        messages[1]["content"],
        messages[1]["tool_calls"],
    ) == (
        3,
        "The planning file is current.",
        [
            {
                "tool_call_id": "call-map-transcript",
                "tool_name": "file_read",
                "status": "completed",
                "token_count": 5,
            }
        ],
    )
    assert [item["fork_sequence_index"] for item in messages] == [0, 3, 4, 5]
    page = app.request(
        f"/api/chat/sessions/{session}/timeline",
        params={"vault_name": vault.name, "before_sequence_index": 3, "page_size": 1},
    )
    assert page.status_code == 200 and [
        (item["sequence_index"], item["through_sequence_index"])
        for item in page.json()["messages"]
    ] == [(1, 3)]
    endpoint = f"/api/chat/sessions/{session}/tools/call-map-transcript"
    scoped = app.request(
        endpoint,
        params={
            "vault_name": vault.name,
            "checkpoint_id": "tool-transcript-checkpoint",
        },
    )
    assert scoped.status_code == 200 and (
        scoped.json()["args"],
        scoped.json()["result_text"],
    ) == ({"operation": "read", "path": "plan.md"}, "Planning evidence.")
    assert app.request(endpoint, params={"vault_name": vault.name}).status_code == 200


async def test_checkpoint_rejects_invalid_evidence_boundaries_atomically(
    app: Application,
) -> None:
    vault = app.vault("MapBoundaryIntegrityVault")
    await app.start()
    from core.memory.session_map.checkpoints import (
        commit_session_map_context_checkpoint,
        load_session_map_observed_through,
    )
    from core.memory.session_map.evidence import SessionMapMessageEvidence
    from core.memory.session_map.models import SessionMapDraft
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session = "checkpoint-boundary-integrity"
    raw = conversation([(f"Question {index}", f"Answer {index}") for index in range(4)])
    seed_session(store, session, vault.name, raw)
    revision, prefix = range_evidence(store, session, vault.name, 0, 1)
    _, missing_start = range_evidence(store, session, vault.name, 2, 3)
    _, skipped = range_evidence(store, session, vault.name, 3, 4)
    recent = (SessionMapMessageEvidence(3, "assistant", "Recent context"),)
    retrieved = (SessionMapMessageEvidence(7, "assistant", "Retrieved context"),)
    for evidence, recent_rows, retrieved_rows, observed in (
        (missing_start, (), (), None),
        ((*prefix, *skipped), (), (), None),
        (prefix, (), (), -1),
        (prefix, (), (), 0),
        (prefix, (), (), True),
        (prefix, recent, (), 1),
        (prefix, (), retrieved, 3),
    ):
        with pytest.raises(ValueError):
            commit_session_map_context_checkpoint(
                store=store,
                session_id=session,
                vault_name=vault.name,
                draft=SessionMapDraft(),
                previous_map=SessionMapDraft(),
                new_evidence=evidence,
                expected_history_revision=revision,
                message_count_before=len(raw),
                source="validation",
                recent_evidence=recent_rows,
                retrieved_evidence=retrieved_rows,
                map_observed_through_sequence_index=observed,
            )
        assert store.get_session_history_revision(session, vault.name) == revision
        assert store.list_context_checkpoints(session, vault.name) == []
    committed = commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=SessionMapDraft(),
        previous_map=SessionMapDraft(),
        new_evidence=prefix,
        expected_history_revision=revision,
        message_count_before=len(raw),
        source="validation",
        recent_evidence=recent,
        retrieved_evidence=retrieved,
    )
    assert load_session_map_observed_through(committed.checkpoint) == 7
    revision = store.get_session_history_revision(session, vault.name)
    for start in (1, 3):
        _, evidence = range_evidence(store, session, vault.name, start, 4)
        with pytest.raises(ValueError):
            commit_session_map_context_checkpoint(
                store=store,
                session_id=session,
                vault_name=vault.name,
                draft=SessionMapDraft(),
                previous_map=SessionMapDraft(),
                new_evidence=evidence,
                expected_history_revision=revision,
                message_count_before=7,
                source="validation",
            )
    _, evidence = range_evidence(store, session, vault.name, 2, 3)
    second = commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=SessionMapDraft(),
        previous_map=SessionMapDraft(),
        new_evidence=evidence,
        expected_history_revision=revision,
        message_count_before=7,
        source="validation",
    )
    assert load_session_map_observed_through(second.checkpoint) == 7
    assert store.get_history(session, vault.name, mode="raw") == raw
