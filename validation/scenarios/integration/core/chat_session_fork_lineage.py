"""Validate canonical session forks with checkpoint lineage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.models import SessionMapDraft  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class ChatSessionForkLineageScenario(BaseScenario):
    """Fork canonical history and only checkpoints safe at the branch point."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("ChatSessionForkLineageVault")
        await self.start_system()
        store = get_runtime_context().chat_store
        session_id = "fork-lineage-source"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        raw_messages = [
            _user("Begin the review."),
            _assistant("I will establish the scope."),
            _user("Compare the two options."),
            _assistant("The comparison is still exploratory."),
            _user("Focus next on cost."),
            _assistant("I will investigate cost without choosing yet."),
            _user("Now include timing."),
            _assistant("Cost and timing are both open."),
        ]
        store.add_messages(session_id, vault.name, raw_messages)
        first_context = _map_context("Initial scope only")
        store.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault.name,
            checkpoint_id="source-map-1",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=8,
            last_message_sequence_index=1,
            summary_message=first_context,
            replacement_history=[first_context],
            replacement_source_sequence_indexes=[None],
            metadata={
                "map_observed_through_sequence_index": 1,
                "map": SessionMapDraft().model_dump(mode="json"),
            },
        )
        second_context = _map_context("Cost and timing synthesis")
        store.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault.name,
            checkpoint_id="source-map-2",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=7,
            last_message_sequence_index=3,
            summary_message=second_context,
            replacement_history=[second_context],
            replacement_source_sequence_indexes=[None],
            metadata={
                "map_observed_through_sequence_index": 7,
                "map": SessionMapDraft().model_dump(mode="json"),
            },
        )

        source_before = store.get_stored_messages(session_id, vault.name, mode="raw")
        response = self.call_api(
            f"/api/chat/sessions/{session_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 5},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        child_id = payload["session"]["session_id"]
        self.soft_assert_equal(
            payload["copied_message_count"],
            6,
            "Fork should report canonical raw messages copied",
        )
        self.soft_assert_equal(
            payload["session"]["context_strategy"],
            "session_map",
            "A safe inherited map checkpoint should pin the child to V2",
        )
        child_raw = store.get_stored_messages(child_id, vault.name, mode="raw")
        self.soft_assert_equal(
            [message.sequence_index for message in child_raw],
            list(range(6)),
            "Child should preserve canonical parent sequence coordinates",
        )
        self.soft_assert_equal(
            [message.message for message in child_raw],
            raw_messages[:6],
            "Child raw history should be the exact canonical source prefix",
        )
        child_checkpoints = store.list_context_checkpoints(child_id, vault.name)
        self.soft_assert_equal(
            len(child_checkpoints),
            1,
            "A future-informed map checkpoint should not cross an earlier fork",
        )
        child_checkpoint = child_checkpoints[0]
        self.soft_assert(
            child_checkpoint.checkpoint_id != "source-map-1",
            "Inherited checkpoints should receive child-owned identifiers",
        )
        child_checkpoint_metadata = json.loads(child_checkpoint.metadata_json or "{}")
        self.soft_assert_equal(
            child_checkpoint_metadata["fork_origin"],
            {
                "source_session_id": session_id,
                "source_checkpoint_id": "source-map-1",
            },
            "Inherited checkpoint metadata should retain explicit origin",
        )
        lineage = store.get_session_metadata(child_id, vault.name)["fork"]
        self.soft_assert_equal(
            lineage["root_session_id"],
            session_id,
            "First-generation fork should record the source as lineage root",
        )
        self.soft_assert_equal(
            lineage["child_owned_from_sequence_index"],
            6,
            "Lineage should identify where independently owned history begins",
        )
        self.soft_assert_equal(
            store.get_stored_messages(session_id, vault.name, mode="raw"),
            source_before,
            "Forking must not mutate canonical source history",
        )
        self.soft_assert_equal(
            len(store.list_context_checkpoints(session_id, vault.name)),
            2,
            "Forking must not mutate source checkpoints",
        )

        nested_response = self.call_api(
            f"/api/chat/sessions/{child_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 5},
        )
        assert nested_response.status_code == 200, nested_response.text
        nested_id = nested_response.json()["session"]["session_id"]
        nested_lineage = store.get_session_metadata(nested_id, vault.name)["fork"]
        self.soft_assert_equal(
            nested_lineage["source_session_id"],
            child_id,
            "Nested lineage should name its immediate parent",
        )
        self.soft_assert_equal(
            nested_lineage["root_session_id"],
            session_id,
            "Nested lineage should retain the original family root",
        )

        latest_response = self.call_api(
            f"/api/chat/sessions/{session_id}/fork",
            method="POST",
            data={"vault_name": vault.name, "through_sequence_index": 7},
        )
        assert latest_response.status_code == 200, latest_response.text
        latest_id = latest_response.json()["session"]["session_id"]
        self.soft_assert_equal(
            len(store.list_context_checkpoints(latest_id, vault.name)),
            2,
            "Forking at the latest observation boundary should inherit all revisions",
        )
        map_response = self.call_api(
            f"/api/chat/sessions/{latest_id}/map?vault_name={vault.name}"
        )
        assert map_response.status_code == 200, map_response.text
        self.soft_assert_equal(
            len(map_response.json()["revisions"]),
            2,
            "Inherited V2 revisions should remain inspectable through the map modal API",
        )


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _map_context(text: str) -> ModelRequest:
    return ModelRequest(parts=[SystemPromptPart(content=text)])
