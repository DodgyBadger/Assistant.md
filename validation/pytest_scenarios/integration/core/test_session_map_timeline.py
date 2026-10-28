"""Mapped sessions retain canonical pagination and historical map inspection."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.session_map_journey_inputs import (
    conversation,
    range_evidence,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_mapped_history_pages_and_historical_checkpoint_remain_accessible(
    app: Application,
) -> None:
    vault = app.vault("SessionMapTimelineVault")
    await app.start()
    from core.memory.session_map.checkpoints import (
        commit_session_map_context_checkpoint,
    )
    from core.memory.session_map.models import (
        SessionMapDraft,
        SessionMapTrajectory,
        SourceRange,
    )
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session = "mapped-timeline"
    raw = conversation(
        [
            ("Review the proposal.", "I will organize the issues."),
            ("The City owns the land.", "That needs legal clarification."),
            ("Draft questions for counsel.", "I drafted the questions."),
        ]
    )
    seed_session(store, session, vault.name, raw)
    revision, evidence = range_evidence(store, session, vault.name, 0, 1)
    first = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="Initial review.", sources=(SourceRange(start=0, end=1),)
        )
    )
    commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=first,
        previous_map=SessionMapDraft(),
        new_evidence=evidence,
        expected_history_revision=revision,
        message_count_before=len(raw),
        source="validation",
        checkpoint_id="first-map-checkpoint",
    )
    revision, evidence = range_evidence(store, session, vault.name, 2, 3)
    second = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="The review concerns land ownership.",
            sources=(SourceRange(start=0, end=1), SourceRange(start=2, end=3)),
        )
    )
    commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=second,
        previous_map=first,
        new_evidence=evidence,
        expected_history_revision=revision,
        message_count_before=5,
        source="validation",
        checkpoint_id="second-map-checkpoint",
    )
    current = app.request(
        f"/api/chat/sessions/{session}/map", params={"vault_name": vault.name}
    )
    assert current.status_code == 200
    data = current.json()
    assert data["revisions"][-1]["map_observed_through_sequence_index"] == 3
    assert data["session_map"]["trajectory"] == second.trajectory.model_dump(
        mode="json"
    )
    assert "transcript" not in data
    detail = app.request(
        f"/api/chat/sessions/{session}", params={"vault_name": vault.name}
    )
    assert detail.status_code == 200
    assert (
        detail.json()["context_checkpoint_kind"],
        detail.json()["context_checkpoint_id"],
    ) == ("session_map", "second-map-checkpoint")
    assert [item["sequence_index"] for item in detail.json()["messages"]] == [
        0,
        1,
        2,
        3,
        4,
        5,
    ]
    page = app.request(
        f"/api/chat/sessions/{session}/timeline",
        params={"vault_name": vault.name, "page_size": 2},
    )
    assert page.status_code == 200
    assert (page.json()["has_older"], page.json()["older_before_sequence_index"]) == (
        True,
        4,
    )
    assert [item["sequence_index"] for item in page.json()["messages"]] == [4, 5]
    older = app.request(
        f"/api/chat/sessions/{session}/timeline",
        params={"vault_name": vault.name, "page_size": 2, "before_sequence_index": 4},
    )
    assert older.status_code == 200 and [
        item["sequence_index"] for item in older.json()["messages"]
    ] == [2, 3]
    historical = app.request(
        f"/api/chat/sessions/{session}/map",
        params={"vault_name": vault.name, "checkpoint_id": "first-map-checkpoint"},
    )
    assert historical.status_code == 200 and historical.json()[
        "session_map"
    ] == first.model_dump(mode="json")
