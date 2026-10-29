"""Legacy map repair and automatic pinned map policy preserve canonical evidence."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.chat_persistence import PERSISTENCE_CONTEXT
from validation.scenarios.support.chat_tasks import chat_turn
from validation.scenarios.support.compaction_journeys import add_turn
from validation.scenarios.support.retained_map_author import retained_author
from validation.scenarios.support.session_map_author_http import configure_author
from validation.scenarios.support.session_map_repair_policy_contracts import (
    configure_map_policy,
    disable_chat_tools,
    repair_author,
    seed_legacy_pending_map,
    settings,
)

pytestmark = pytest.mark.scenario


async def test_oldest_map_invalid_then_valid_repair_consumes_pending_once(
    app: Application,
):
    vault = app.vault("OldestMapRepair")
    await app.start()
    from core.memory.session_map.checkpoints import (
        load_session_map_checkpoint,
        load_session_map_pending_evidence,
    )
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    session = "oldest-map-repair"
    with repair_author() as server:
        configure_author(app, server)
        configure_map_policy(app)
        seed_legacy_pending_map(store, vault.name, session)
        before = store.get_stored_messages(session, vault.name, mode="raw")
        checkpoints = store.list_context_checkpoints(session, vault.name)
        revision = store.get_session_history_revision(session, vault.name)
        assert load_session_map_checkpoint(checkpoints[-1]).schema_version == 1
        assert load_session_map_pending_evidence(checkpoints[-1]) is not None
        server.invalid = True
        rejected = await app.request_async(
            f"/api/chat/sessions/{session}/compact", "POST", {"vault_name": vault.name}
        )
        assert rejected.status_code == 500
        assert store.list_context_checkpoints(session, vault.name) == checkpoints
        assert store.get_stored_messages(session, vault.name, mode="raw") == before
        assert store.get_session_history_revision(session, vault.name) == revision
        failed_requests = len(server.requests)
        assert failed_requests > 1
        server.invalid = False
        valid = await app.request_async(
            f"/api/chat/sessions/{session}/compact", "POST", {"vault_name": vault.name}
        )
        assert valid.status_code == 200, valid.text
        payload = server.payloads[failed_requests]
        assert [
            (item["source_range"]["start"], item["source_range"]["end"])
            for item in payload["new_evidence_envelopes"]
        ][0] == (2, 5)
        assert any(
            item["sequence_index"] == 0
            for item in payload["retrieved_canonical_evidence"]
        )
        repaired = store.get_latest_context_checkpoint(session, vault.name)
        assert repaired.checkpoint_id != checkpoints[-1].checkpoint_id
        assert load_session_map_pending_evidence(repaired) is None
        draft = load_session_map_checkpoint(repaired)
        assert draft.schema_version == 3
        assert [(source.start, source.end) for source in draft.entries[0].sources] == [
            (0, 0)
        ]
        assert store.get_stored_messages(session, vault.name, mode="raw") == before
        add_turn(store, vault.name, session, "A later question.", "A later answer.")
        add_turn(
            store,
            vault.name,
            session,
            "Another later question.",
            "Another later answer.",
        )
        offset = len(server.payloads)
        again = await app.request_async(
            f"/api/chat/sessions/{session}/compact", "POST", {"vault_name": vault.name}
        )
        assert again.status_code == 200, again.text
        intervals = [
            item["source_range"]
            for item in server.payloads[offset]["new_evidence_envelopes"]
        ]
        assert intervals and all(item["start"] >= 10 for item in intervals)
        latest = store.get_latest_context_checkpoint(session, vault.name)
        assert (
            latest.checkpoint_id != repaired.checkpoint_id
            and load_session_map_pending_evidence(latest) is None
        )
        assert (
            store.get_stored_messages(session, vault.name, mode="raw")[: len(before)]
            == before
        )
        app.evidence.save_json("oldest-map-repair-author-inputs.json", server.payloads)


async def test_auto_retained_map_pins_strategy_task_chain_and_no_fallback(
    app: Application,
):
    vault = app.vault("AutomaticRetainedMap")
    app.write(vault, "AssistantMD/Authoring/full_history.md", PERSISTENCE_CONTEXT)
    with retained_author() as server:
        await app.start()
        from core.memory.session_map.checkpoints import load_session_map_checkpoint
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        configure_map_policy(app, low=500, high=1000)
        disable_chat_tools(app)
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "automatic-retained-map"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        add_turn(
            store,
            vault.name,
            session,
            "Create the deployment README. " * 800,
            "README work is still pending. " * 800,
        )
        first = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "The deployment README is now complete.",
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert first["terminal_event"]["event"] == "done"
        assert [
            item["sequence_index"]
            for item in server.payloads[0]["retained_recent_evidence"]
        ] == [2, 3]
        checkpoint = store.get_latest_context_checkpoint(session, vault.name)
        assert checkpoint.checkpoint_kind == "session_map"
        assert load_session_map_checkpoint(checkpoint).entries[0].sources[0].start == 2
        metadata = json.loads(checkpoint.metadata_json)
        assert (
            metadata["consumed_through_sequence_index"] == 1
            and metadata["map_observed_through_sequence_index"] == 3
        )
        settings(app, compaction_strategy="recovery_card")
        add_turn(
            store,
            vault.name,
            session,
            "Continue with the next deployment phase. " * 800,
            "The next deployment phase remains open. " * 800,
        )
        second = await chat_turn(
            app,
            {
                "vault_name": vault.name,
                "session_id": session,
                "prompt": "Continue from the completed README.",
                "model": "test",
                "context_template": "full_history.md",
            },
        )
        assert second["terminal_event"]["event"] == "done"
        assert len(server.payloads) == 2
        latest = store.get_latest_context_checkpoint(session, vault.name)
        assert (
            latest.checkpoint_kind == "session_map"
            and latest.checkpoint_id != checkpoint.checkpoint_id
        )
        assert load_session_map_checkpoint(latest).entries[0].sources[0].start == 2
        compactions = await runtime.task_coordinator.list_tasks(
            kind="history_compaction"
        )
        authors = await runtime.task_coordinator.list_tasks(
            kind="session_map_authoring"
        )
        assert len(compactions) == len(authors) == 2
        for turn in (first, second):
            parent = next(
                task
                for task in compactions
                if task.parent_task_id == turn["task"]["task_id"]
            )
            child = next(
                task for task in authors if task.parent_task_id == parent.task_id
            )
            assert parent.status == child.status == "completed"
        add_turn(
            store,
            vault.name,
            session,
            "Preserve this unfinished deployment phase. " * 800,
            "More deployment evidence remains to consider. " * 800,
        )
        raw = store.get_stored_messages(session, vault.name, mode="raw")
        deleted = app.request("/api/system/secrets/SESSION_MAP_AUTHOR_URL", "DELETE")
        assert deleted.status_code == 200
        for index in range(2):
            turn = await chat_turn(
                app,
                {
                    "vault_name": vault.name,
                    "session_id": session,
                    "prompt": f"Keep the saved map usable, turn {index}.",
                    "model": "test",
                    "context_template": "full_history.md",
                },
            )
            assert turn["terminal_event"]["event"] == "done"
        assert len(server.payloads) == 2
        assert store.get_latest_context_checkpoint(session, vault.name) == latest
        assert (
            store.get_stored_messages(session, vault.name, mode="raw")[: len(raw)]
            == raw
        )
        assert all(
            item.checkpoint_kind == "session_map"
            for item in store.list_context_checkpoints(session, vault.name)
        )
        activity = app.request("/api/system/activity-log", params={"limit": 1000})
        assert activity.status_code == 200
        deferred = [
            entry["data"]
            for entry in activity.json()["entries"]
            if entry.get("data", {}).get("event") == "compaction_strategy_selected"
            and entry.get("data", {}).get("session_id") == session
            and entry.get("data", {}).get("status") == "deferred"
        ]
        assert len(deferred) == 2
        assert all(
            item["strategy"] == "session_map"
            and item["configured_strategy"] == "recovery_card"
            and item["reason"] == "author_model_unavailable"
            for item in deferred
        )
        app.evidence.save_json("automatic-retained-map-inputs.json", server.payloads)


async def test_automatic_map_high_watermark_is_inclusive(app: Application):
    vault = app.vault("MapWatermarkEquality")
    await app.start()
    from core.chat.compaction import (
        get_compaction_status,
        maybe_auto_compact_after_turn,
    )
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.runtime.state import get_runtime_context

    with use_execution_authority(LOCAL_USER_AUTHORITY), repair_author() as server:
        configure_author(app, server)
        configure_map_policy(app)
        store = get_runtime_context().chat_store
        session = "map-equality"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        add_turn(
            store,
            vault.name,
            session,
            "A completed earlier question. " * 100,
            "Its completed answer. " * 100,
        )
        add_turn(
            store,
            vault.name,
            session,
            "A completed latest question. " * 100,
            "Its latest answer. " * 100,
        )
        status = await get_compaction_status(
            session_id=session, vault_name=vault.name, store=store
        )
        tokens = status.estimated_tokens_before
        settings(app, compaction_high_watermark_tokens=tokens + 1)
        below = await get_compaction_status(
            session_id=session, vault_name=vault.name, store=store
        )
        assert below.recommended is False
        assert (
            await maybe_auto_compact_after_turn(
                session_id=session, vault_name=vault.name, vault_path=str(vault)
            )
            is None
        )
        assert not server.requests
        settings(app, compaction_high_watermark_tokens=tokens)
        equal = await get_compaction_status(
            session_id=session, vault_name=vault.name, store=store
        )
        assert (
            equal.estimated_tokens_before == equal.compaction_high_watermark_tokens
            and equal.recommended
        )
        result = await maybe_auto_compact_after_turn(
            session_id=session, vault_name=vault.name, vault_path=str(vault)
        )
        assert result is not None and result.checkpoint_id
        assert len(server.requests) == 1
