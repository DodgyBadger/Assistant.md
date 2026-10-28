"""Retained completion evidence participates in a real session-map update."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.compaction_journeys import add_turn
from validation.scenarios.support.retained_map_author import retained_author
from validation.scenarios.support.session_map_author_http import configure_author

pytestmark = pytest.mark.scenario


async def test_retained_completion_authors_citable_map(app: Application) -> None:
    vault = app.vault("SessionMapRetainedEvidenceVault")
    with retained_author() as server:
        await app.start()
        from core.memory.session_map.checkpoints import load_session_map_checkpoint
        from core.runtime.execution_tasks import ExecutionTaskKind
        from core.runtime.state import get_runtime_context

        configure_author(app, server)
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_low_watermark_tokens", "500"),
            ("compaction_retained_turns", "1"),
            ("compaction_high_watermark_tokens", "1000"),
            ("compaction_type", "auto"),
            ("compaction_author_thinking", "low"),
        ):
            response = app.request(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200, response.text
        runtime = get_runtime_context()
        store = runtime.chat_store
        session = "session-map-retained-evidence"
        store.ensure_session(session, vault.name, owner_principal_id="local-user")
        add_turn(
            store,
            vault.name,
            session,
            "Create the deployment README. " * 800,
            "The active next action is to create the README. " * 800,
        )
        add_turn(
            store,
            vault.name,
            session,
            "The deployment README is now complete.",
            "Confirmed; no README work remains.",
        )
        response = await app.request_async(
            f"/api/chat/sessions/{session}/compact",
            method="POST",
            data={"vault_name": vault.name},
        )
        assert response.status_code == 200, response.text
        assert len(server.payloads) == 1
        payload = server.payloads[0]
        assert [item["source_range"] for item in payload["new_evidence_envelopes"]] == [
            {"start": 0, "end": 1}
        ]
        assert [
            item["sequence_index"] for item in payload["retained_recent_evidence"]
        ] == [2, 3]
        assert [
            item["source_range"] for item in payload["retained_recent_evidence"]
        ] == [{"start": 2, "end": 2}, {"start": 3, "end": 3}]
        checkpoint = store.get_latest_context_checkpoint(session, vault.name)
        assert checkpoint is not None
        meta = json.loads(checkpoint.metadata_json or "{}")
        assert (
            meta["consumed_through_sequence_index"] == 1
            and meta["map_observed_through_sequence_index"] == 3
        )
        draft = load_session_map_checkpoint(checkpoint)
        assert (
            draft.entries[0].id == "deployment_readme"
            and draft.entries[0].kind == "artifact"
        )
        assert (
            draft.entries[0].sources[0].start == 2
            and draft.entries[0].sources[0].end == 2
        )
        assert len(store.get_history(session, vault.name) or []) == 3
        assert len(store.get_history(session, vault.name, mode="raw") or []) == 4
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        assert [task.status for task in tasks] == ["completed"] and tasks[0].metadata[
            "recent_evidence_message_count"
        ] == 2
        app.evidence.save_json("retained-map-author-payload.json", payload)
