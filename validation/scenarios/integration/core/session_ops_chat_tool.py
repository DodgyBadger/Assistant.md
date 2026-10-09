"""
Integration scenario for the chat-facing session_ops tool.

Validates that a selected chat agent can call session_ops through the normal
tool path and use active chat session/vault context for session summaries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from validation.core.base_scenario import BaseScenario, with_local_user_authority


class SessionOpsChatToolScenario(BaseScenario):
    """Validate session_ops can write and read session summary from chat."""

    @with_local_user_authority
    async def test_scenario(self):
        vault = self.create_vault("SessionOpsChatToolVault")
        session_id = "session_ops_chat_tool_session"

        await self.start_system()

        from pydantic_ai.messages import ModelResponse, ToolCallPart
        from pydantic_ai.models.test import TestModel

        import core.chat.executor as chat_executor
        from core.authoring.shared.tool_binding import resolve_tool_binding
        from core.chat.chat_store import ChatStore
        from core.memory.session_summary import SessionSummaryStore

        store = SessionSummaryStore(
            system_root=str(self._get_system_controller()._system_root)
        )
        chat_store = ChatStore(
            system_root=str(self._get_system_controller()._system_root)
        )

        current_case = {"name": "upsert"}

        class _SessionOpsToolModel(TestModel):
            def __init__(self):
                super().__init__(call_tools=["session_ops"])
                self.tool_requested = False

            def _request(self, messages, model_settings, model_request_parameters):
                # TestModel otherwise skips tools when any prior response exists.
                # Drive a fresh real tool call on every chat turn in this scenario.
                if not self.tool_requested:
                    self.tool_requested = True
                    tool_def = model_request_parameters.function_tools[0]
                    return ModelResponse(
                        parts=[
                            ToolCallPart(
                                "session_ops",
                                self.gen_tool_args(tool_def),
                                tool_call_id=uuid4().hex,
                            )
                        ]
                    )
                return super()._request(
                    messages, model_settings, model_request_parameters
                )

            def gen_tool_args(self, tool_def):
                if getattr(tool_def, "name", "") != "session_ops":
                    return super().gen_tool_args(tool_def)
                if current_case["name"] == "upsert":
                    return {
                        "operation": "upsert_session_summary",
                        "data": {
                            "summary": "Chat summary testing",
                            "domain": "validation",
                            "work_product": "test artifact",
                            "user_intent": "Validate that chat can write a session summary.",
                        },
                    }
                if current_case["name"] == "get":
                    return {"operation": "get_session_summary"}
                if current_case["name"] == "summarize_failure":
                    return {"operation": "summarize_session"}
                raise AssertionError(
                    f"Unexpected session_ops case: {current_case['name']}"
                )

        def _patched_prepare_agent_config(
            vault_name, vault_path, tools, model, thinking=None, chat_mode=None
        ):
            del vault_name, tools, model, thinking
            binding = resolve_tool_binding(["session_ops"], vault_path=vault_path)
            return (
                "You must call session_ops before responding.",
                binding.tool_instructions,
                _SessionOpsToolModel(),
                binding.tool_functions,
            )

        original_prepare = chat_executor._prepare_agent_config
        chat_executor._prepare_agent_config = _patched_prepare_agent_config
        try:
            upserted = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Write a summary for this chat session.",
                    "session_id": session_id,
                    "workspace_path": "Projects/WorkspaceA",
                    "tools": ["session_ops"],
                    "model": "test",
                },
            )
            self.soft_assert_equal(
                upserted["start_response"].status_code,
                200,
                "Summary chat task should start",
            )
            self.soft_assert_equal(
                upserted["terminal_event"].get("event"),
                "done",
                "Summary chat should succeed",
            )
            current = store.get_session_summary(
                vault_name=vault.name, session_id=session_id
            )
            self.soft_assert(
                current is not None and current.summary == "Chat summary testing",
                "session_ops should write a session summary for the active chat session",
            )
            self.soft_assert_equal(
                current.workspace_path if current else None,
                "Projects/WorkspaceA",
                "session_ops should copy the active session workspace path onto the summary",
            )

            updated = self.call_api(
                f"/api/chat/sessions/{session_id}/summary",
                method="PUT",
                params={"vault_name": vault.name},
                data={
                    "summary": "Wetland grant planning notes from manual workspace.",
                    "domain": "validation",
                    "work_product": "test artifact",
                    "user_intent": "Validate that chat can write a session summary.",
                    "workspace_path": "Projects/ManualWorkspace",
                    "named_entities": "",
                    "source_summary": "",
                    "metadata": current.metadata if current else {},
                },
            )
            self.soft_assert_equal(
                updated.status_code, 200, "Manual summary update should succeed"
            )
            updated_payload = updated.json()
            self.soft_assert_equal(
                updated_payload.get("workspace_path"),
                "Projects/ManualWorkspace",
                "Manual summary updates should persist workspace_path",
            )
            store.upsert_session_summary(
                vault_name=vault.name,
                session_id=session_id,
                title="Manual workspace test",
                summary="Wetland grant planning notes from manual workspace.",
                domain="validation",
                work_product="test artifact",
                user_intent="Validate that chat can write a session summary.",
                workspace_path="Projects/ManualWorkspace",
                metadata=current.metadata if current else {},
            )

            sibling_session_id = "session_ops_chat_tool_sibling"
            child_session_id = "session_ops_chat_tool_child"
            chat_store.ensure_session(
                sibling_session_id,
                vault.name,
                owner_principal_id="local-user",
            )
            chat_store.ensure_session(
                child_session_id,
                vault.name,
                owner_principal_id="local-user",
            )
            store.upsert_session_summary(
                vault_name=vault.name,
                session_id=sibling_session_id,
                title="Sibling workspace test",
                summary="Wetland grant planning notes from another workspace.",
                domain="validation",
                work_product="test artifact",
                user_intent="Validate workspace-filtered session search.",
                workspace_path="Projects/OtherWorkspace",
            )
            store.upsert_session_summary(
                vault_name=vault.name,
                session_id=child_session_id,
                title="Child workspace test",
                summary="Wetland grant planning notes from a child workspace.",
                domain="validation",
                work_product="test artifact",
                user_intent="Validate workspace subtree filtering.",
                workspace_path="Projects/ManualWorkspace/Child",
            )

            from core.tools.session_ops import (
                _list_sessions,
                _parse_session_filter,
                _search_session_summary_fields,
                _search_sessions,
            )

            listed = _list_sessions(
                vault_name=vault.name,
                limit=10,
                cursor="",
                summary_status="any",
            )
            listed_session = next(
                (
                    row
                    for row in listed.get("sessions", [])
                    if row.get("session_id") == session_id
                ),
                None,
            )
            self.soft_assert(
                listed_session is not None,
                "session_ops list_sessions should include the active session",
            )
            self.soft_assert(
                listed_session is not None
                and listed_session.get("workspace_path") == "Projects/ManualWorkspace",
                "session_ops list_sessions should expose workspace_path in compact rows",
            )
            exact_filter = _parse_session_filter(
                {"workspace": "Projects/ManualWorkspace"},
                vault_name=vault.name,
                active_session_id=session_id,
            )
            exact_listed = _list_sessions(
                vault_name=vault.name,
                limit=10,
                cursor="",
                summary_status="any",
                workspace_filter=exact_filter,
            )
            self.soft_assert_equal(
                [row.get("session_id") for row in exact_listed.get("sessions", [])],
                [session_id],
                "session_ops list_sessions should support exact workspace filtering",
            )
            subtree_filter = _parse_session_filter(
                {"workspace": "Projects/ManualWorkspace/*"},
                vault_name=vault.name,
                active_session_id=session_id,
            )
            subtree_listed = _list_sessions(
                vault_name=vault.name,
                limit=10,
                cursor="",
                summary_status="any",
                workspace_filter=subtree_filter,
            )
            self.soft_assert_equal(
                [row.get("session_id") for row in subtree_listed.get("sessions", [])],
                [child_session_id],
                "session_ops list_sessions should support workspace subtree filtering",
            )

            original_search_by_field = (
                SessionSummaryStore.search_session_summaries_by_field
            )

            async def _no_vector_matches(self, **kwargs):
                del self, kwargs
                return ()

            SessionSummaryStore.search_session_summaries_by_field = _no_vector_matches
            try:
                filtered_candidates = await _search_session_summary_fields(
                    store=store,
                    vault_name=vault.name,
                    query="wetland grant planning",
                    limit=10,
                    workspace_filter=exact_filter,
                )
                self.soft_assert_equal(
                    sorted(filtered_candidates),
                    [session_id],
                    "session_ops search should apply workspace filter before ranking candidates",
                )
                boosted_search = await _search_sessions(
                    store=store,
                    vault_name=vault.name,
                    mode="search",
                    query="wetland grant planning",
                    limit=10,
                    active_workspace_path="Projects/ManualWorkspace",
                )
                first_match = (
                    boosted_search.get("matches", [{}])[0]
                    if boosted_search.get("matches")
                    else {}
                )
                self.soft_assert_equal(
                    first_match.get("session_id"),
                    session_id,
                    "session_ops search_sessions should boost exact current-workspace matches",
                )
                self.soft_assert(
                    any(
                        evidence.get("source") == "workspace"
                        for evidence in first_match.get("evidence", [])
                    ),
                    "workspace boost should be visible as search evidence",
                )
            finally:
                SessionSummaryStore.search_session_summaries_by_field = (
                    original_search_by_field
                )

            current_case["name"] = "get"
            fetched = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Fetch the current session summary.",
                    "session_id": session_id,
                    "tools": ["session_ops"],
                    "model": "test",
                },
            )
            self.soft_assert_equal(
                fetched["start_response"].status_code,
                200,
                "Fetch chat task should start",
            )
            self.soft_assert_equal(
                fetched["terminal_event"].get("event"),
                "done",
                "Fetch chat should succeed",
            )

            # Failed preflight must remain visible in durable activity, while
            # neither diagnostics nor chat tool results expose exception values.
            current_case["name"] = "summarize_failure"
            from core.vector import VectorService

            for error, diagnostic_code in (
                (
                    TypeError("process() takes no keyword arguments"),
                    "brotli_decoder_incompatible",
                ),
                (RuntimeError("private-embedding-error-payload"), None),
            ):
                with patch.object(
                    VectorService, "embed_documents", AsyncMock(side_effect=error)
                ):
                    failed = await self.run_chat_task(
                        {
                            "vault_name": vault.name,
                            "prompt": "Refresh the current session summary.",
                            "session_id": session_id,
                            "tools": ["session_ops"],
                            "model": "test",
                        }
                    )
                assert failed["terminal_event"].get("event") == "done"
                activity = self.call_api("/api/system/activity-log?limit=200")
                assert activity.status_code == 200
                entries = activity.json()["entries"]
                for event_name in (
                    "session_ops_failed",
                    "session_summary_embedding_preflight_failed",
                ):
                    matching = [
                        entry["data"]
                        for entry in entries
                        if entry.get("data", {}).get("event") == event_name
                        and entry.get("data", {}).get("error_type")
                        == type(error).__name__
                    ]
                    assert matching
                    assert all(
                        row["status"] == "failed" and row["traceback"]
                        for row in matching
                    )
                    assert all(
                        row.get("diagnostic_code") == diagnostic_code
                        for row in matching
                    )
                assert "private-embedding-error-payload" not in json.dumps(entries)
                assert "private-embedding-error-payload" not in str(
                    chat_store.get_stored_messages(session_id, vault.name)
                )
                assert (
                    store.get_session_summary(
                        vault_name=vault.name, session_id=session_id
                    ).summary
                    == "Wetland grant planning notes from manual workspace."
                )

            deleted = self.call_api(
                f"/api/chat/sessions/{session_id}",
                method="DELETE",
                params={"vault_name": vault.name},
            )
            self.soft_assert_equal(
                deleted.status_code, 200, "Delete chat should succeed"
            )
            after_delete = store.get_session_summary(
                vault_name=vault.name, session_id=session_id
            )
            self.soft_assert(
                after_delete is None,
                "Deleting a chat session should delete the matching stored summary",
            )
        finally:
            chat_executor._prepare_agent_config = original_prepare
            await self.stop_system()
            self.teardown_scenario()

        self.assert_no_failures()
