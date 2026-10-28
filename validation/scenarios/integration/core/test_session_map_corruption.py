"""Corrupt map checkpoints fail closed without leaking persisted contents."""

from __future__ import annotations

import json
import sqlite3

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_map_journey_inputs import (
    conversation,
    range_evidence,
    seed_session,
)

pytestmark = pytest.mark.scenario


async def test_corrupt_map_and_replacement_are_identified_without_private_data(
    app: Application,
) -> None:
    for secret in ("PRIVATE_CORRUPT_MAP_SENTINEL", "private-checkpoint-contents"):
        app.evidence.register_secret(secret)
    vault = app.vault("SessionMapCorruptionVault")
    await app.start()
    from core.chat.chat_store import ChatHistoryCorruptionError
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
    session = "corrupt-map-session"
    raw = conversation(
        [("First question", "First answer"), ("Next question", "Next answer")]
    )
    seed_session(store, session, vault.name, raw)
    revision, evidence = range_evidence(store, session, vault.name, 0, 1)
    draft = SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="An initial exchange.", sources=(SourceRange(start=0, end=1),)
        )
    )
    commit_session_map_context_checkpoint(
        store=store,
        session_id=session,
        vault_name=vault.name,
        draft=draft,
        previous_map=SessionMapDraft(),
        new_evidence=evidence,
        expected_history_revision=revision,
        message_count_before=len(raw),
        source="validation",
        checkpoint_id="corruption-checkpoint",
    )
    checkpoint = store.get_latest_context_checkpoint(session, vault.name)
    assert checkpoint is not None
    effective = store.get_history(session, vault.name)
    current_revision = store.get_session_history_revision(session, vault.name)
    database = app.root / "system/chat_sessions.db"
    try:
        for payload in (
            {
                **json.loads(checkpoint.metadata_json),
                "map": {"trajectory": "PRIVATE_CORRUPT_MAP_SENTINEL"},
            },
            ["PRIVATE_CORRUPT_MAP_SENTINEL"],
        ):
            with sqlite3.connect(database) as conn:
                conn.execute(
                    "UPDATE chat_compaction_checkpoints SET metadata_json = ? WHERE checkpoint_id = ?",
                    (json.dumps(payload), checkpoint.checkpoint_id),
                )
            response = app.request(
                f"/api/chat/sessions/{session}/map", params={"vault_name": vault.name}
            )
            assert (
                response.status_code == 500
                and response.json()["error"] == "SessionMapCheckpointCorrupt"
            )
            assert (
                response.json()["details"]["checkpoint_id"] == checkpoint.checkpoint_id
            )
            assert "PRIVATE_CORRUPT_MAP_SENTINEL" not in response.text
        with sqlite3.connect(database) as conn:
            conn.execute(
                "UPDATE chat_compaction_checkpoints SET metadata_json = ?, replacement_history_json = ? WHERE checkpoint_id = ?",
                (
                    checkpoint.metadata_json,
                    json.dumps({"private": "private-checkpoint-contents"}),
                    checkpoint.checkpoint_id,
                ),
            )
        with pytest.raises(ChatHistoryCorruptionError) as failure:
            store.get_history(session, vault.name)
        assert (
            failure.value.session_id,
            failure.value.vault_name,
            failure.value.checkpoint_id,
        ) == (session, vault.name, checkpoint.checkpoint_id)
        assert store.get_history(session, vault.name, mode="raw") == raw
        assert (
            store.get_session_history_revision(session, vault.name) == current_revision
        )
    finally:
        with sqlite3.connect(database) as conn:
            conn.execute(
                "UPDATE chat_compaction_checkpoints SET metadata_json = ?, replacement_history_json = ? WHERE checkpoint_id = ?",
                (
                    checkpoint.metadata_json,
                    checkpoint.replacement_history_json,
                    checkpoint.checkpoint_id,
                ),
            )
    assert store.get_history(session, vault.name) == effective
    activity = app.request("/api/system/activity-log?limit=200")
    assert activity.status_code == 200
    assert (
        "PRIVATE_CORRUPT_MAP_SENTINEL" not in activity.text
        and "private-checkpoint-contents" not in activity.text
    )
    entries = activity.json()["entries"]
    failures = [
        entry["data"]
        for entry in entries
        if entry.get("data", {}).get("event") == "session_map_checkpoint_load_failed"
    ]
    assert failures and all(
        row["status"] == "failed"
        and row["session_id"] == session
        and row["checkpoint_id"] == checkpoint.checkpoint_id
        and row["vault_name"] == vault.name
        and row["error"]
        for row in failures
    )
    assert any(
        entry.get("data", {}).get("event") == "chat_history_deserialization_failed"
        and entry["data"].get("session_id") == session
        and entry["data"].get("checkpoint_id") == checkpoint.checkpoint_id
        and entry["data"].get("error_type") == "ChatHistoryCorruptionError"
        for entry in entries
    )
