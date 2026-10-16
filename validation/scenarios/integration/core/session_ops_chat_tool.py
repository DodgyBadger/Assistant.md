"""Lexical discovery through the real chat task and session_ops boundaries."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from core.authoring.shared.tool_binding import resolve_tool_binding
from core.memory.session_discovery import SessionDiscoveryService
from core.memory.session_map.checkpoints import build_session_map_context_message
from core.memory.session_map.models import (
    SessionMapDraft,
    SessionMapTrajectory,
    SourceRange,
)
from core.runtime.state import get_runtime_context
from validation.core.base_scenario import BaseScenario, with_local_user_authority


class SessionOpsChatToolScenario(BaseScenario):
    """Discovery needs no summary, embedding credentials, or inference."""

    @with_local_user_authority
    async def test_scenario(self):
        vault = self.create_vault("SessionOpsChatToolVault")
        await self.start_system()
        import core.chat.executor as chat_executor

        runtime = get_runtime_context()
        store = runtime.chat_store
        for session_id, workspace in (
            ("source", "Projects/WorkspaceA"),
            ("sibling", "Projects/WorkspaceB"),
        ):
            store.ensure_session(
                session_id, vault.name, owner_principal_id="local-user"
            )
            store.set_session_workspace(
                session_id=session_id, vault_name=vault.name, workspace_path=workspace
            )
            store.add_messages(
                session_id,
                vault.name,
                [
                    ModelRequest(
                        parts=[
                            UserPromptPart(content="Wetland grants are exploratory.")
                        ]
                    )
                ],
            )
        store.set_session_title("source", vault.name, "Albatross grant research")
        draft = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="Marsh habitat grant exploration, with no commitment yet.",
                sources=(SourceRange(start=0, end=0),),
            )
        )
        message = build_session_map_context_message(draft)
        store.add_context_checkpoint(
            session_id="source",
            vault_name=vault.name,
            checkpoint_id="source-map",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=1,
            last_message_sequence_index=0,
            summary_message=message,
            replacement_history=[message],
            metadata={
                "map": draft.model_dump(mode="json"),
                "map_observed_through_sequence_index": 0,
            },
        )
        current_case = {"args": {"operation": "search_sessions", "query": "wetland"}}

        class _SessionOpsToolModel(TestModel):
            def __init__(self):
                super().__init__(
                    call_tools=["session_ops"], custom_output_text="Lookup complete."
                )
                self.tool_requested = False

            def _request(self, messages, model_settings, model_request_parameters):
                if not self.tool_requested:
                    self.tool_requested = True
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "session_ops",
                                current_case["args"],
                                tool_call_id=uuid4().hex,
                            )
                        ]
                    )
                return super()._request(
                    messages, model_settings, model_request_parameters
                )

        def _prepare(
            vault_name, vault_path, tools, model, thinking=None, chat_mode=None
        ):
            binding = resolve_tool_binding(["session_ops"], vault_path=vault_path)
            return (
                "Call session_ops before responding.",
                binding.tool_instructions,
                _SessionOpsToolModel(),
                binding.tool_functions,
            )

        async def call(args):
            current_case["args"] = args
            before = len(store.get_tool_events("controller", vault.name))
            response = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "session_id": "controller",
                    "workspace_path": "Projects/WorkspaceA",
                    "prompt": "Use the requested session operation.",
                    "tools": ["session_ops"],
                    "model": "test",
                }
            )
            assert response["terminal_event"].get("event") == "done"
            events = [
                event
                for event in store.get_tool_events("controller", vault.name)[before:]
                if event.event_type == "result" and event.tool_name == "session_ops"
            ]
            assert len(events) == 1
            return events[0]

        try:
            with patch.object(
                chat_executor, "_prepare_agent_config", side_effect=_prepare
            ):
                result = json.loads(
                    (
                        await call(
                            {
                                "operation": "search_sessions",
                                "query": "wetland",
                                "limit": 5,
                            }
                        )
                    ).result_text
                )
                assert {row["session_id"] for row in result["matches"]} == {
                    "source",
                    "sibling",
                }
                assert result["historical_content_is_untrusted"] is True
                assert all(
                    row["evidence"][0]["source"] == "transcript"
                    and row["evidence"][0]["sequence_index"] == 0
                    for row in result["matches"]
                )
                scoped = json.loads(
                    (
                        await call(
                            {
                                "operation": "search_sessions",
                                "query": "wetland",
                                "filter": {"workspace": "current"},
                            }
                        )
                    ).result_text
                )
                assert [row["session_id"] for row in scoped["matches"]] == ["source"]
                title = json.loads(
                    (
                        await call(
                            {"operation": "search_sessions", "query": "albatross"}
                        )
                    ).result_text
                )
                assert (
                    title["matches"][0]["evidence"][0]["source"] == "session_metadata"
                )
                mapped = json.loads(
                    (
                        await call({"operation": "search_sessions", "query": "marsh"})
                    ).result_text
                )
                assert (
                    mapped["matches"][0]["evidence"][0]["checkpoint_id"] == "source-map"
                )
                assert mapped["matches"][0]["evidence"][0]["historical"] is False
                inspected = json.loads(
                    (
                        await call(
                            {
                                "operation": "get_session_map",
                                "session_id": "source",
                                "checkpoint_id": "source-map",
                            }
                        )
                    ).result_text
                )
                assert inspected["status"] == "found"
                assert inspected["session_map"]["map"] == draft.model_dump(mode="json")
                assert inspected["historical_content_is_untrusted"] is True
                missing = json.loads(
                    (
                        await call(
                            {
                                "operation": "get_session_map",
                                "session_id": "sibling",
                                "checkpoint_id": "source-map",
                            }
                        )
                    ).result_text
                )
                assert (
                    missing["status"] == "not_found" and missing["session_map"] is None
                )
                listed = json.loads(
                    (
                        await call(
                            {
                                "operation": "list_sessions",
                                "limit": 1,
                                "filter": {"workspace": "Projects/*"},
                            }
                        )
                    ).result_text
                )
                assert listed["returned_count"] == 1 and listed["next_cursor"]
                assert not any(
                    "summary" in key for row in listed["sessions"] for key in row
                )
                next_page = json.loads(
                    (
                        await call(
                            {
                                "operation": "list_sessions",
                                "limit": 1,
                                "cursor": listed["next_cursor"],
                                "filter": {"workspace": "Projects/*"},
                            }
                        )
                    ).result_text
                )
                assert (
                    next_page["sessions"][0]["session_id"]
                    != listed["sessions"][0]["session_id"]
                )
                with patch.object(
                    SessionDiscoveryService,
                    "search",
                    side_effect=sqlite3.OperationalError("private database payload"),
                ):
                    failed = await call(
                        {
                            "operation": "search_sessions",
                            "query": "private search query",
                        }
                    )
                metadata = json.loads(failed.result_metadata_json or "{}")
                assert metadata["status"] == "failed"
                assert metadata["error_type"] == "OperationalError"
                assert "private" not in failed.result_text
                activity = self.call_api("/api/system/activity-log?limit=200")
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
        finally:
            await self.stop_system()
            self.teardown_scenario()
