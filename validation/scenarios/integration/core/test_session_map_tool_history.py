"""Map history retrieval preserves chronology, provenance, and lean selection."""

from __future__ import annotations

import json
import sqlite3

import pytest
from pydantic_ai import ModelRetry

from validation.support.application import Application
from validation.support.memory.session_discovery import (
    add_discovery_checkpoint,
    seed_discovery_sessions,
)
from validation.support.memory.session_map_tools import call_map_tool, set_map_setting

pytestmark = pytest.mark.scenario


async def test_map_discovery_snapshot_and_selected_history(app: Application) -> None:
    vault = app.vault("MapHistoryVault")
    await app.start()
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    seed_discovery_sessions(store, vault.name)
    # Equal and misleading timestamps must never determine revision chronology.
    with sqlite3.connect(app.root / "system/chat_sessions.db") as connection:
        connection.execute(
            "UPDATE chat_compaction_checkpoints SET created_at = ? WHERE checkpoint_id = ?",
            ("2099-01-01", "old-map"),
        )
        connection.execute(
            "UPDATE chat_compaction_checkpoints SET created_at = ? WHERE checkpoint_id = ?",
            ("2000-01-01", "new-map"),
        )
    first = await call_map_tool(app, vault.name, operation="list_session_maps", limit=1)
    assert first["order"] == "oldest_first"
    assert [row["checkpoint_id"] for row in first["session_maps"]] == ["old-map"]
    assert first["session_maps"][0]["revision"] == 1
    assert first["session_maps"][0]["entry_counts"] == {"option": 1}
    assert first["session_maps"][0]["trajectory_excerpt"]
    assert "map" not in first["session_maps"][0]
    assert first["total_count"] == 2
    add_discovery_checkpoint(store, vault.name, "later-map", 3, "Later exploration")
    second = await call_map_tool(
        app,
        vault.name,
        operation="list_session_maps",
        limit=1,
        cursor=first["next_cursor"],
    )
    assert [row["checkpoint_id"] for row in second["session_maps"]] == ["new-map"]
    assert second["session_maps"][0]["revision"] == 2
    assert second["next_cursor"] is None and second["total_count"] == 2
    fresh = await call_map_tool(app, vault.name, operation="list_session_maps")
    assert [row["revision"] for row in fresh["session_maps"]] == [1, 2, 3]
    assert fresh["session_maps"][-1]["is_current"] is True
    assert fresh["session_maps"][0]["historical"] is True

    maps = await call_map_tool(
        app,
        vault.name,
        operation="get_session_map",
        checkpoint_ids=["new-map", "old-map"],
        fields=["trajectory", "entries"],
        entry_kinds=["option"],
    )
    assert maps["order"] == "oldest_first"
    assert [row["checkpoint_id"] for row in maps["session_maps"]] == [
        "old-map",
        "new-map",
    ]
    for revision, row in enumerate(maps["session_maps"], 1):
        boundary = 1 if revision == 1 else 3
        assert row["session_id"] == "long" and row["revision"] == revision
        assert row["observed_through_sequence_index"] == boundary
        assert row["compacted_through_sequence_index"] == boundary
        assert row["map"]["trajectory"]["sources"] == [{"start": 0, "end": boundary}]
        entry = row["map"]["entries"][0]
        assert set(entry) == {"id", "kind", "state", "basis", "text", "sources"}
        assert entry["sources"] == [{"start": 0, "end": boundary}]
    trajectory = await call_map_tool(
        app,
        vault.name,
        operation="get_session_map",
        checkpoint_id="old-map",
        fields=["trajectory"],
    )
    assert "entries" not in trajectory["session_maps"][0]["map"]
    assert trajectory["session_maps"][0]["map"]["schema_version"] == 3
    empty = await call_map_tool(
        app,
        vault.name,
        operation="get_session_map",
        fields=["entries"],
        entry_kinds=["decision"],
    )
    assert empty["session_maps"][0]["map"]["entries"] == []
    latest = await call_map_tool(app, vault.name, operation="get_session_map")
    assert latest["session_maps"][0]["checkpoint_id"] == "later-map"
    # A map overview is not the whole conversation: follow canonical references
    # and inspect the suffix after the latest observation boundary.
    window = await call_map_tool(
        app,
        vault.name,
        operation="get_transcript_window",
        sequence_index=4,
        before=0,
        after=0,
    )
    assert (
        window["messages"][0]["sequence_index"]
        > latest["session_maps"][0]["observed_through_sequence_index"]
    )
    assert "kestrel" in window["messages"][0]["content"]


async def test_map_retrieval_authority_forks_and_empty_results(
    app: Application,
) -> None:
    vault = app.vault("MapAccessVault")
    await app.start()
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    seed_discovery_sessions(store, vault.name)
    for operation in ("list_session_maps", "get_session_map"):
        empty = await call_map_tool(
            app, vault.name, operation=operation, session_id="short"
        )
        assert empty["session_maps"] == [] and empty["guidance"]
        with pytest.raises(ModelRetry) as hidden:
            await call_map_tool(
                app, vault.name, operation=operation, session_id="hidden"
            )
        with pytest.raises(ModelRetry) as absent:
            await call_map_tool(
                app, vault.name, operation=operation, session_id="absent"
            )
        assert str(hidden.value) == str(absent.value)
    for ids in (["old-map", "missing"], ["old-map", "source-map"]):
        with pytest.raises(ModelRetry):
            await call_map_tool(
                app, vault.name, operation="get_session_map", checkpoint_ids=ids
            )
    store.fork_session(
        source_session_id="long",
        new_session_id="fork",
        vault_name=vault.name,
        through_sequence_index=1,
        title="Earlier fork",
    )
    fork = await call_map_tool(
        app, vault.name, operation="list_session_maps", session_id="fork"
    )
    assert len(fork["session_maps"]) == 1
    inherited = fork["session_maps"][0]
    assert inherited["checkpoint_id"] != "old-map" and inherited["revision"] == 1
    assert inherited["observed_through_sequence_index"] <= 1
    with pytest.raises(ModelRetry):
        await call_map_tool(
            app,
            vault.name,
            operation="get_session_map",
            session_id="fork",
            checkpoint_id="new-map",
        )
    page = await call_map_tool(app, vault.name, operation="list_session_maps", limit=1)
    with pytest.raises(ModelRetry):
        await call_map_tool(
            app,
            vault.name,
            operation="list_session_maps",
            session_id="fork",
            cursor=page["next_cursor"],
        )
    with pytest.raises(ModelRetry):
        await call_map_tool(
            app, vault.name, operation="list_session_maps", cursor="not-a-cursor"
        )


@pytest.mark.parametrize(
    "arguments,parameter,correction",
    [
        ({"fields": ["decisions"]}, "fields", "entries"),
        ({"fields": []}, "fields", "omit"),
        ({"entry_kinds": ["invalid"]}, "entry_kinds", "decision"),
        ({"entry_kinds": []}, "entry_kinds", "omit"),
        (
            {"fields": ["trajectory"], "entry_kinds": ["decision"]},
            "entry_kinds",
            "entries",
        ),
        (
            {"checkpoint_id": "old-map", "checkpoint_ids": ["new-map"]},
            "checkpoint",
            "either",
        ),
        ({"checkpoint_ids": []}, "checkpoint_ids", "omit"),
    ],
)
async def test_map_input_errors_are_actionable(
    app: Application, arguments: dict, parameter: str, correction: str
) -> None:
    vault = app.vault("MapErrorsVault")
    await app.start()
    from core.runtime.state import get_runtime_context

    seed_discovery_sessions(get_runtime_context().chat_store, vault.name)
    with pytest.raises(ModelRetry) as error:
        await call_map_tool(app, vault.name, operation="get_session_map", **arguments)
    message = str(error.value)
    assert parameter in message and correction in message.lower()


async def test_map_limits_are_live_validated_settings(app: Application) -> None:
    vault = app.vault("MapLimitsVault")
    await app.start()
    from core.runtime.state import get_runtime_context

    store = get_runtime_context().chat_store
    seed_discovery_sessions(store, vault.name)
    for index in range(20):
        add_discovery_checkpoint(
            store, vault.name, f"extra-{index}", 3, f"Exploration {index}"
        )
    default_page = await call_map_tool(app, vault.name, operation="list_session_maps")
    assert len(default_page["session_maps"]) == 20
    ids = [row["checkpoint_id"] for row in default_page["session_maps"]]
    trajectories = await call_map_tool(
        app,
        vault.name,
        operation="get_session_map",
        checkpoint_ids=ids,
        fields=["trajectory"],
    )
    assert len(trajectories["session_maps"]) == 20
    with pytest.raises(ModelRetry) as capped:
        await call_map_tool(
            app, vault.name, operation="get_session_map", checkpoint_ids=ids[:6]
        )
    assert "5" in str(capped.value)
    with pytest.raises(ModelRetry):
        await call_map_tool(
            app,
            vault.name,
            operation="get_session_map",
            checkpoint_ids=ids + ["extra-19"],
            fields=["trajectory"],
        )
    for name, value in (
        ("session_map_history_page_size", 1),
        ("session_map_history_max_page_size", 2),
        ("session_map_trajectory_max_maps", 2),
        ("session_map_entries_max_maps", 1),
    ):
        set_map_setting(app, name, value)
    assert (
        len(
            (await call_map_tool(app, vault.name, operation="list_session_maps"))[
                "session_maps"
            ]
        )
        == 1
    )
    for arguments in (
        {"operation": "list_session_maps", "limit": 3},
        {"operation": "get_session_map", "checkpoint_ids": ids[:2]},
        {
            "operation": "get_session_map",
            "checkpoint_ids": ids[:3],
            "fields": ["trajectory"],
        },
    ):
        with pytest.raises(ModelRetry):
            await call_map_tool(app, vault.name, **arguments)
    for name, value in (
        ("session_map_entries_max_maps", "0"),
        ("session_map_trajectory_max_maps", "1.5"),
        ("session_map_history_page_size", "3"),
    ):
        rejected = app.request(
            f"/api/system/settings/general/{name}", "PUT", {"value": value}
        )
        assert rejected.status_code == 400, rejected.text
    saved = app.request("/api/system/settings/general").json()
    assert "session_map_history_page_size" in json.dumps(saved)
