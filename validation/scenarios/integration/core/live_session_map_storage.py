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
            GoalEntry,
            GoalStatus,
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
