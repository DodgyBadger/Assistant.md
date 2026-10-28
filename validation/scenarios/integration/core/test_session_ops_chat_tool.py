"""Lexical discovery through a configured local provider and real chat tool calls."""

from __future__ import annotations

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.session_ops_chat import (
    PRIVATE_QUERY,
    break_discovery_index,
    call_session_ops,
    configure_provider,
    seed_session_ops_inputs,
    session_ops_provider,
)

pytestmark = pytest.mark.scenario


async def test_session_ops_chat_discovery_and_safe_failure(app: Application) -> None:
    vault = app.vault("SessionOpsChatToolVault")
    (vault / "Projects/WorkspaceA").mkdir(parents=True)
    with session_ops_provider() as server:
        await app.start()
        from core.runtime.state import get_runtime_context

        configure_provider(app, server)
        draft = seed_session_ops_inputs(get_runtime_context().chat_store, vault.name)
        result = json.loads(
            (
                await call_session_ops(
                    app,
                    server,
                    vault.name,
                    {"operation": "search_sessions", "query": "wetland", "limit": 5},
                )
            ).result_text
        )
        assert {row["session_id"] for row in result["matches"]} == {"source", "sibling"}
        assert result["historical_content_is_untrusted"] is True
        assert all(
            row["evidence"][0]["source"] == "transcript"
            and row["evidence"][0]["sequence_index"] == 0
            for row in result["matches"]
        )
        scoped = json.loads(
            (
                await call_session_ops(
                    app,
                    server,
                    vault.name,
                    {
                        "operation": "search_sessions",
                        "query": "wetland",
                        "filter": {"workspace": "current"},
                    },
                )
            ).result_text
        )
        assert [row["session_id"] for row in scoped["matches"]] == ["source"]
        title = json.loads(
            (
                await call_session_ops(
                    app,
                    server,
                    vault.name,
                    {"operation": "search_sessions", "query": "albatross"},
                )
            ).result_text
        )
        assert title["matches"][0]["evidence"][0]["source"] == "session_metadata"
        mapped = json.loads(
            (
                await call_session_ops(
                    app,
                    server,
                    vault.name,
                    {"operation": "search_sessions", "query": "marsh"},
                )
            ).result_text
        )
        assert mapped["matches"][0]["evidence"][0]["checkpoint_id"] == "source-map"
        assert mapped["matches"][0]["evidence"][0]["historical"] is False
        inspected = json.loads(
            (
                await call_session_ops(
                    app,
                    server,
                    vault.name,
                    {
                        "operation": "get_session_map",
                        "session_id": "source",
                        "checkpoint_id": "source-map",
                    },
                )
            ).result_text
        )
        assert inspected["status"] == "found"
        assert inspected["session_map"]["map"] == draft
        assert inspected["historical_content_is_untrusted"] is True
        missing = json.loads(
            (
                await call_session_ops(
                    app,
                    server,
                    vault.name,
                    {
                        "operation": "get_session_map",
                        "session_id": "sibling",
                        "checkpoint_id": "source-map",
                    },
                )
            ).result_text
        )
        assert missing["status"] == "not_found" and missing["session_map"] is None
        listed = json.loads(
            (
                await call_session_ops(
                    app,
                    server,
                    vault.name,
                    {
                        "operation": "list_sessions",
                        "limit": 1,
                        "filter": {"workspace": "Projects/*"},
                    },
                )
            ).result_text
        )
        assert listed["returned_count"] == 1 and listed["next_cursor"]
        assert not any("summary" in key for row in listed["sessions"] for key in row)
        next_page = json.loads(
            (
                await call_session_ops(
                    app,
                    server,
                    vault.name,
                    {
                        "operation": "list_sessions",
                        "limit": 1,
                        "cursor": listed["next_cursor"],
                        "filter": {"workspace": "Projects/*"},
                    },
                )
            ).result_text
        )
        assert (
            next_page["sessions"][0]["session_id"]
            != listed["sessions"][0]["session_id"]
        )
        break_discovery_index(app)
        failed = await call_session_ops(
            app,
            server,
            vault.name,
            {"operation": "search_sessions", "query": "private search query"},
        )
        metadata = json.loads(failed.result_metadata_json or "{}")
        assert metadata["status"] == "failed"
        assert metadata["error_type"] == "OperationalError"
        assert "private" not in failed.result_text
        activity = app.request("/api/system/activity-log?limit=200")
        assert activity.status_code == 200
        failures = [
            entry
            for entry in activity.json()["entries"]
            if entry.get("data", {}).get("event") == "session_ops_failed"
            and entry.get("data", {}).get("tool_call_id") == failed.tool_call_id
        ]
        assert len(failures) == 1
        data = failures[0]["data"]
        assert data["run_id"] and data["issue"]
        assert data["operation"] == "search_sessions"
        assert "private" not in json.dumps(failures)
        assert server.calls, "Expected actual configured provider HTTP requests"
        app.events.require(
            "tool_invoked", tool="session_ops", operation="search_sessions"
        )
        # Inspect actual production log output before evidence redaction.
        for entry in activity.json()["entries"]:
            if entry.get("data", {}).get("event") == "session_ops_failed":
                serialized = json.dumps(entry)
                assert PRIVATE_QUERY not in serialized
