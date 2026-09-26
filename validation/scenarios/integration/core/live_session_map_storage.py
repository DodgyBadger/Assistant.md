"""Integration contract for deterministic live session-map persistence."""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import ModelRequest, UserPromptPart

from validation.core.base_scenario import BaseScenario


class LiveSessionMapStorageScenario(BaseScenario):
    """Validate chat-transaction coupling, revisions, forks, and purge."""

    async def test_scenario(self):
        vault = self.create_vault("LiveSessionMapStorageVault")
        await self.start_system()

        from core.chat.chat_store import ChatStore
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.memory.session_map.models import (
            AddPatch,
            Attention,
            ChangeAttentionPatch,
            EpistemicStatus,
            GoalEntry,
            GoalStatus,
            ObservationEntry,
            Relevance,
            SessionMap,
            SourceRef,
        )
        from core.memory.session_map.store import SessionMapStore
        from core.runtime.state import get_runtime_context

        runtime = get_runtime_context()
        chat_store = ChatStore(str(runtime.config.system_root))
        map_store = SessionMapStore(str(runtime.config.system_root))
        session_id = "live-session-map-storage"
        chat_store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )

        with chat_store.transaction() as conn:
            chat_store.add_messages(
                session_id,
                vault.name,
                [ModelRequest(parts=[UserPromptPart(content="Build the map")])],
                connection=conn,
            )
            map_store.record_pending(
                conn,
                session_id=session_id,
                vault_name=vault.name,
                through_sequence_index=0,
                token_count=4,
            )

        frozen = map_store.freeze_attempt(session_id, vault.name)
        self.soft_assert_equal(
            frozen.frozen_from_sequence_index,
            0,
            "The first frozen attempt starts at the first canonical message",
        )
        now = datetime.now(UTC)
        goal = GoalEntry(
            id="goal_1",
            text="Build the deterministic live session map.",
            status=GoalStatus.ACTIVE,
            source_refs=(SourceRef(sequence_index=0, role="user"),),
        )
        operations = (
            AddPatch(entry=goal),
            ChangeAttentionPatch(
                active_goal_ids=(goal.id,),
                evidence_refs=(SourceRef(sequence_index=0, role="user"),),
            ),
        )
        session_map = SessionMap(
            session_id=session_id,
            revision=1,
            updated_through_sequence_index=0,
            observed_source_content_revision=1,
            created_at=now,
            updated_at=now,
            attention=Attention(
                active_goal_ids=(goal.id,), changed_at_sequence_index=0
            ),
            goals=(goal,),
        )
        map_store.commit_revision(
            session_id=session_id,
            vault_name=vault.name,
            expected_revision=0,
            session_map=session_map,
            operations=operations,
        )
        self.soft_assert_equal(
            map_store.get_latest_revision(session_id, vault.name),
            session_map,
            "The committed map round-trips as the latest revision",
        )
        self.soft_assert_equal(
            map_store.get_revision_operations(session_id, vault.name, revision=1),
            operations,
            "The append-only revision retains its exact patch audit",
        )

        with chat_store.transaction() as conn:
            chat_store.add_messages(
                session_id,
                vault.name,
                [ModelRequest(parts=[UserPromptPart(content="Inspect map history")])],
                connection=conn,
            )
            map_store.record_pending(
                conn,
                session_id=session_id,
                vault_name=vault.name,
                through_sequence_index=1,
                token_count=3,
            )

        map_store.freeze_attempt(session_id, vault.name)
        observation = ObservationEntry(
            id="observation_1",
            text="The durable map can be inspected independently of telemetry.",
            epistemic_status=EpistemicStatus.OBSERVED,
            relevance=Relevance.SUPPORTING,
            source_refs=(SourceRef(sequence_index=1, role="user"),),
        )
        second_operations = (AddPatch(entry=observation),)
        second_map = session_map.model_copy(
            update={
                "revision": 2,
                "updated_through_sequence_index": 1,
                "observed_source_content_revision": 2,
                "updated_at": datetime.now(UTC),
                "observations": (observation,),
            }
        )
        map_store.commit_revision(
            session_id=session_id,
            vault_name=vault.name,
            expected_revision=1,
            session_map=second_map,
            operations=second_operations,
            decision={"outcome": "author"},
            authoring={"model": "test"},
        )

        sessions_response = self.call_api(f"/api/chat/sessions?vault_name={vault.name}")
        self.soft_assert_equal(
            sessions_response.status_code,
            200,
            "Session listing succeeds for map inspection",
        )
        listed_session = next(
            item
            for item in sessions_response.json()
            if item["session_id"] == session_id
        )
        self.soft_assert_equal(
            listed_session.get("has_session_map"),
            True,
            "Session listing advertises committed session maps",
        )

        latest_response = self.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name={vault.name}"
        )
        self.soft_assert_equal(
            latest_response.status_code,
            200,
            "Session-map inspection endpoint succeeds",
        )
        latest_payload = latest_response.json()
        self.soft_assert_equal(
            latest_payload.get("selected_revision"),
            2,
            "Session-map inspection defaults to the latest revision",
        )
        self.soft_assert_equal(
            [item["revision"] for item in latest_payload.get("revisions", [])],
            [2, 1],
            "Session-map revision history is newest first",
        )
        self.soft_assert_equal(
            latest_payload.get("maintenance", {}).get("status"),
            "idle",
            "Session-map inspection exposes durable maintenance state",
        )

        historical_response = self.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name={vault.name}&revision=1"
        )
        self.soft_assert_equal(
            historical_response.status_code,
            200,
            "A historical session-map revision can be selected",
        )
        historical_payload = historical_response.json()
        self.soft_assert_equal(
            historical_payload.get("session_map", {}).get("revision"),
            1,
            "Historical inspection returns the requested immutable map",
        )
        self.soft_assert_equal(
            len(historical_payload.get("operations", [])),
            2,
            "Historical inspection includes its patch audit",
        )
        missing_revision_response = self.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name={vault.name}&revision=99"
        )
        self.soft_assert_equal(
            missing_revision_response.status_code,
            404,
            "An unknown session-map revision is not silently substituted",
        )
        wrong_vault_response = self.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name=AnotherVault"
        )
        self.soft_assert_equal(
            wrong_vault_response.status_code,
            409,
            "Session-map inspection enforces the canonical session vault",
        )

        fork_id = "live-session-map-storage-fork"
        chat_store.fork_session(
            source_session_id=session_id,
            new_session_id=fork_id,
            vault_name=vault.name,
            through_sequence_index=0,
            title=None,
        )
        self.soft_assert(
            map_store.get_latest_revision(fork_id, vault.name) is None,
            "A fork rebuilds rather than inheriting source-linked map state",
        )

        chat_store.delete_sessions(vault.name, session_id=session_id)
        self.soft_assert(
            map_store.get_latest_revision(session_id, vault.name) is None,
            "Deleting a chat cascades to its map revisions",
        )
        self.soft_assert(
            map_store.get_maintenance_state(session_id, vault.name) is None,
            "Deleting a chat cascades to its maintenance state",
        )

        self.assert_no_failures()
