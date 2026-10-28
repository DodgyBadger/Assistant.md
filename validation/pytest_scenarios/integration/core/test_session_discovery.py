"""Discovery reads real canonical fixtures through API and authorized service boundaries."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.session_discovery import (
    add_discovery_checkpoint,
    seed_discovery_sessions,
)

pytestmark = pytest.mark.scenario


async def test_session_discovery_indexes_and_authorization(app: Application) -> None:
    vault = app.vault("DiscoveryVault")
    await app.start()
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.memory.session_discovery import SessionDiscoveryService
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    store = runtime.chat_store
    seed_discovery_sessions(store, vault.name)
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        discovery = SessionDiscoveryService(store, runtime.chat_session_access)

        found = app.request(
            f"/api/chat/sessions/search?vault_name={vault.name}&query=otter"
        ).json()
        assert found["limit"] == 20
        assert [hit["session"]["session_id"] for hit in found["matches"]] == ["short"]
        assert found["matches"][0]["evidence"][0]["source"] == "transcript"
        historical = app.request(
            f"/api/chat/sessions/search?vault_name={vault.name}&query=quokka"
        ).json()
        assert historical["matches"][0]["evidence"][0]["checkpoint_id"] == "old-map"
        assert historical["matches"][0]["evidence"][0]["historical"] is True
        assert (
            app.request(
                f"/api/chat/sessions/search?vault_name={vault.name}&query=otter&limit=21"
            ).status_code
            == 422
        )
        assert (
            app.request(
                f"/api/chat/sessions/search?vault_name={vault.name}&query=%20"
            ).status_code
            == 400
        )
        listed = app.request(f"/api/chat/sessions?vault_name={vault.name}").json()
        assert {row["session_id"] for row in listed} == {"short", "long"}

        assert (
            discovery.search(vault_name=vault.name, query="albatross")[0]["session_id"]
            == "short"
        )
        assert (
            discovery.search(vault_name=vault.name, query="otter")[0]["evidence"][0][
                "source"
            ]
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
        assert discovery.get_map(vault_name="another-vault", session_id="long") is None
        assert (
            discovery.get_map(
                vault_name=vault.name, session_id="short", checkpoint_id="old-map"
            )
            is None
        )
        assert (
            discovery.search(vault_name=vault.name, query="kestrel")[0]["session_id"]
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
            for row in discovery.search(vault_name=vault.name, query="albatross otter")
        )

        # Repeated map hits remain one session, not a revision-count reward.
        baseline = discovery.search(vault_name=vault.name, query="proposal")[0]["score"]
        add_discovery_checkpoint(
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
        activity = app.request("/api/system/activity-log?limit=200").json()
        assert not any(
            entry.get("data", {}).get("event") == "session_discovery_completed"
            for entry in activity["entries"]
        ), "Typing/search reads must not fill System Activity with helper successes"
        app.events.require(
            "session_discovery_completed",
            status="completed",
            vault_name=vault.name,
        )
