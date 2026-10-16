"""Embedding-free discovery over metadata, map revisions, and canonical history."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from core.memory.session_map.checkpoints import build_session_map_context_message
from core.memory.session_map.models import (
    SessionMapDraft,
    SessionMapEntry,
    SessionMapTrajectory,
    SourceRange,
)
from core.runtime.state import get_runtime_context
from validation.core.base_scenario import BaseScenario, with_local_user_authority


class SessionDiscoveryScenario(BaseScenario):
    @with_local_user_authority
    async def test_scenario(self):
        from core.memory.session_discovery import SessionDiscoveryService

        vault = self.create_vault("DiscoveryVault")
        await self.start_system()
        try:
            runtime = get_runtime_context()
            store = runtime.chat_store
            store.ensure_session("short", vault.name, owner_principal_id="local-user")
            store.set_session_title("short", vault.name, "Albatross research")
            store.add_messages(
                "short", vault.name, [_user("Looking for otter habitats.")]
            )
            store.set_session_workspace(
                session_id="short",
                vault_name=vault.name,
                workspace_path="Projects/Birds",
            )
            store.ensure_session("long", vault.name, owner_principal_id="local-user")
            store.set_session_workspace(
                session_id="long", vault_name=vault.name, workspace_path="Projects/City"
            )
            store.add_messages(
                "long",
                vault.name,
                [
                    _user("Explore redevelopment alternatives."),
                    ModelResponse(
                        parts=[TextPart("We are considering several possibilities.")]
                    ),
                    _user("Compare the current proposal."),
                    ModelResponse(parts=[TextPart("We have not decided yet.")]),
                    _user("New tail topic: kestrel habitat."),
                ],
            )
            self._checkpoint(
                store, vault.name, "old-map", 1, "Quokka feasibility review"
            )
            self._checkpoint(
                store, vault.name, "new-map", 3, "Current proposal remains exploratory"
            )
            store.ensure_session(
                "hidden", vault.name, owner_principal_id="another-user"
            )
            store.set_session_title("hidden", vault.name, "Albatross private")
            store.add_messages("hidden", vault.name, [_user("Private otter study.")])
            discovery = SessionDiscoveryService(store, runtime.chat_session_access)

            found = self.call_api(
                f"/api/chat/sessions/search?vault_name={vault.name}&query=otter"
            ).json()
            assert found["limit"] == 20
            assert [hit["session"]["session_id"] for hit in found["matches"]] == [
                "short"
            ]
            assert found["matches"][0]["evidence"][0]["source"] == "transcript"
            historical = self.call_api(
                f"/api/chat/sessions/search?vault_name={vault.name}&query=quokka"
            ).json()
            assert historical["matches"][0]["evidence"][0]["checkpoint_id"] == "old-map"
            assert historical["matches"][0]["evidence"][0]["historical"] is True
            assert (
                self.call_api(
                    f"/api/chat/sessions/search?vault_name={vault.name}&query=otter&limit=21"
                ).status_code
                == 422
            )
            assert (
                self.call_api(
                    f"/api/chat/sessions/search?vault_name={vault.name}&query=%20"
                ).status_code
                == 400
            )
            listed = self.call_api(f"/api/chat/sessions?vault_name={vault.name}").json()
            assert {row["session_id"] for row in listed} == {"short", "long"}

            assert (
                discovery.search(vault_name=vault.name, query="albatross")[0][
                    "session_id"
                ]
                == "short"
            )
            assert (
                discovery.search(vault_name=vault.name, query="otter")[0]["evidence"][
                    0
                ]["source"]
                == "transcript"
            )
            old = discovery.search(vault_name=vault.name, query="quokka")
            assert len(old) == 1 and old[0]["session_id"] == "long"
            assert old[0]["evidence"][0]["checkpoint_id"] == "old-map"
            assert old[0]["evidence"][0]["historical"] is True
            old_map = discovery.get_map(
                vault_name=vault.name, session_id="long", checkpoint_id="old-map"
            )
            assert old_map is not None and old_map["historical"] is True
            assert old_map["map"]["trajectory"]["sources"] == [{"start": 0, "end": 1}]
            assert discovery.get_map(vault_name=vault.name, session_id="hidden") is None
            assert (
                discovery.get_map(vault_name="another-vault", session_id="long") is None
            )
            assert (
                discovery.get_map(
                    vault_name=vault.name, session_id="short", checkpoint_id="old-map"
                )
                is None
            )
            assert (
                discovery.search(vault_name=vault.name, query="kestrel")[0][
                    "session_id"
                ]
                == "long"
            )
            assert not discovery.search(
                vault_name=vault.name, query="quokka", workspace="Projects/Birds"
            )
            assert discovery.search(
                vault_name=vault.name,
                query="otter",
                workspace="Projects",
                workspace_prefix=True,
            )
            assert all(
                row["session_id"] != "hidden"
                for row in discovery.search(
                    vault_name=vault.name, query="albatross otter"
                )
            )

            # Repeated map hits remain one session, not a revision-count reward.
            baseline = discovery.search(vault_name=vault.name, query="proposal")[0][
                "score"
            ]
            self._checkpoint(
                store,
                vault.name,
                "repeat-map",
                3,
                "Current proposal remains exploratory",
            )
            repeated = discovery.search(vault_name=vault.name, query="proposal")
            assert len(repeated) == 1
            assert (
                len(
                    [
                        hit
                        for hit in repeated[0]["evidence"]
                        if hit["source"] == "session_map"
                    ]
                )
                == 1
            )
            assert repeated[0]["score"] == baseline

            store.fork_session(
                source_session_id="long",
                new_session_id="fork",
                vault_name=vault.name,
                through_sequence_index=3,
                title="Forked work",
            )
            assert {
                row["session_id"]
                for row in discovery.search(vault_name=vault.name, query="quokka")
            } == {"long", "fork"}
            store.set_session_title("short", vault.name, "Cormorant research")
            assert not discovery.search(vault_name=vault.name, query="albatross")
            assert discovery.search(vault_name=vault.name, query="cormorant")

            # Simulate an installation that predates the discovery indexes.
            with sqlite3.connect(Path(store.system_root) / "chat_sessions.db") as conn:
                conn.execute("DROP TABLE chat_session_metadata_fts")
                conn.execute("DROP TABLE chat_session_maps_fts")
            from core.chat.chat_store import ChatStore

            rebuilt = SessionDiscoveryService(
                ChatStore(system_root=store.system_root), runtime.chat_session_access
            )
            assert rebuilt.search(vault_name=vault.name, query="quokka")
            store.delete_sessions(vault.name, session_id="long")
            store.delete_sessions(vault.name, session_id="fork")
            assert not discovery.search(vault_name=vault.name, query="quokka")
            assert "private" not in json.dumps(
                discovery.search(vault_name=vault.name, query="otter")
            )
            activity = self.call_api("/api/system/activity-log?limit=200").json()
            assert not any(
                entry.get("data", {}).get("event") == "session_discovery_completed"
                for entry in activity["entries"]
            ), "Typing/search reads must not fill System Activity with helper successes"
            self.assert_event_contains(
                self.events_since(0),
                name="session_discovery_completed",
                expected={"status": "completed", "vault_name": vault.name},
            )
        finally:
            await self.stop_system()
            self.teardown_scenario()

    @staticmethod
    def _checkpoint(store, vault_name, checkpoint_id, through, text):
        draft = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text=text, sources=(SourceRange(start=0, end=through),)
            ),
            entries=(
                SessionMapEntry(
                    id="proposal_exploration",
                    kind="option",
                    state="active",
                    basis="user_established",
                    text=text,
                    sources=(SourceRange(start=0, end=through),),
                ),
            ),
        )
        message = build_session_map_context_message(draft)
        store.add_context_checkpoint(
            session_id="long",
            vault_name=vault_name,
            checkpoint_id=checkpoint_id,
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=5,
            last_message_sequence_index=through,
            summary_message=message,
            replacement_history=[message],
            metadata={
                "map": draft.model_dump(mode="json"),
                "map_observed_through_sequence_index": through,
            },
        )


def _user(text):
    return ModelRequest(parts=[UserPromptPart(content=text)])
