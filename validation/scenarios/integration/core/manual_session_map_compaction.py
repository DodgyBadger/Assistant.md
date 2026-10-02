"""Validate strategy-aware manual Compaction v2 through the public API."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from core.identity import LOCAL_USER_PRINCIPAL_ID, ExecutionAuthority  # noqa: E402
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SessionMapTrajectory,
    SourceRange,
)
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    ExecutionTaskSource,
    chat_session_scope,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from core.runtime.task_runner import ExecutionTaskSpec  # noqa: E402
from core.tools.chat_history_compact import ChatHistoryCompact  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class ManualSessionMapCompactionScenario(BaseScenario):
    """Keep policy, strategy pinning, focus, and task ownership distinct."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("ManualSessionMapCompactionVault")
        await self.start_system()

        import core.memory.session_map.service as session_map_service

        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_author_model", "test"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "50"),
            ("compaction_retained_turns", "1"),
            ("compaction_high_watermark_tokens", "100000"),
            ("compaction_type", "suggested"),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200, f"{key} setting should update"

        runtime = get_runtime_context()
        store = runtime.chat_store
        session_id = "manual-session-map-compaction"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                _user("Explore whether the redevelopment needs legal review."),
                _assistant("Legal review is one option, not yet a decision."),
                _user("Compare that option with an internal policy review."),
                _assistant("Both routes remain open."),
                _user("The land ownership question is still unresolved."),
                _assistant("I will keep it as an open question."),
                _user("For now, prepare the comparison only."),
                _assistant("I will preserve that current direction."),
            ],
        )

        captured_prompts: list[dict[str, object]] = []

        async def authored_map(**kwargs: object) -> SessionMapDraft:
            payload = json.loads(str(kwargs["prompt"]))
            captured_prompts.append(payload)
            sources = tuple(
                SourceRange(**item["source_range"])
                for item in payload["new_evidence_envelopes"]
            )
            return SessionMapDraft(
                trajectory=SessionMapTrajectory(
                    text="The session is comparing review routes without adopting one.",
                    sources=sources,
                ),
                entries=(
                    SessionMapEntry(
                        id="review_route_comparison",
                        kind="option",
                        state="active",
                        basis="mixed",
                        text="Legal and internal policy review remain alternatives.",
                        sources=sources,
                    ),
                ),
            )

        original_author = session_map_service._invoke_session_map_model
        session_map_service._invoke_session_map_model = authored_map
        try:
            status_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compaction-status?vault_name={vault.name}"
            )
            first_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compact",
                method="POST",
                data={
                    "vault_name": vault.name,
                    "focus": "Emphasize that the review routes remain options.",
                },
            )

            for key, value in (
                ("compaction_strategy", "recovery_card"),
                ("compaction_type", "none"),
            ):
                response = self.call_api(
                    f"/api/system/settings/general/{key}",
                    method="PUT",
                    data={"value": value},
                )
                assert response.status_code == 200, f"{key} setting should update"
            store.add_messages(
                session_id,
                vault.name,
                [
                    _user("Add cost as another comparison dimension."),
                    _assistant("Cost is now part of the comparison."),
                    _user("Do not choose a route yet."),
                    _assistant("Both routes remain options."),
                ],
            )
            second_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compact",
                method="POST",
                data={"vault_name": vault.name},
            )
            unavailable_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compact",
                method="POST",
                data={"vault_name": vault.name},
            )
            tool = ChatHistoryCompact.get_tool(str(vault))
            context = SimpleNamespace(
                deps=SimpleNamespace(session_id=session_id, vault_name=vault.name)
            )

            async def invoke_tool(_task: object) -> str:
                return await tool.function(context, operation="compact")

            tool_result = await runtime.task_runner.run_inline(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.CHAT,
                    scope=chat_session_scope(session_id),
                    source=ExecutionTaskSource.API,
                    label="Compaction tool parent",
                    authority=ExecutionAuthority(LOCAL_USER_PRINCIPAL_ID),
                ),
                invoke_tool,
            )
        finally:
            session_map_service._invoke_session_map_model = original_author

        self.soft_assert_equal(
            status_response.status_code,
            200,
            "Compaction status should remain available under suggested policy",
        )
        status = status_response.json()
        self.soft_assert_equal(
            (
                status["strategy"],
                status["recommended"],
                status["manual_compaction_available"],
            ),
            ("session_map", False, True),
            "Status should distinguish manual availability from the automatic threshold",
        )
        self.soft_assert_equal(
            first_response.status_code,
            200,
            "Suggested policy should permit explicit session-map compaction",
        )
        self.soft_assert_equal(
            second_response.status_code,
            200,
            "None policy should still permit an explicitly approved compaction",
        )
        self.soft_assert_equal(
            (
                unavailable_response.status_code,
                unavailable_response.json().get("status"),
                unavailable_response.json().get("reason"),
            ),
            (200, "unavailable", "retained_turn_floor"),
            "A request with no safely evictable group should return a stable no-op",
        )
        self.soft_assert_equal(
            (
                first_response.json().get("strategy"),
                second_response.json().get("strategy"),
            ),
            ("session_map", "session_map"),
            "The first checkpoint should pin later manual compactions to session_map",
        )
        self.soft_assert_equal(
            captured_prompts[0].get("user_focus"),
            "Emphasize that the review routes remain options.",
            "Manual focus should reach the map author as a separate prompt field",
        )
        self.soft_assert_equal(
            captured_prompts[1].get("user_focus"),
            None,
            "Focus should apply only to the requested rewrite",
        )

        history_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        author_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        api_history_tasks = [task for task in history_tasks if task.source == "api"]
        tool_history_tasks = [task for task in history_tasks if task.source == "tool"]
        history_ids = {task.task_id for task in history_tasks}
        self.soft_assert_equal(
            len(api_history_tasks),
            3,
            "Each API request should own one history-compaction task",
        )
        self.soft_assert(
            all(task.result is not None for task in history_tasks),
            "Compaction tasks should retain their bounded result payloads",
        )
        self.soft_assert_equal(
            len(tool_history_tasks),
            1,
            "The chat tool should reuse the governed compaction operation",
        )
        parent = await runtime.task_coordinator.get_task(
            tool_history_tasks[0].parent_task_id
        )
        self.soft_assert(
            parent is not None and parent.kind == ExecutionTaskKind.CHAT.value,
            "Tool compaction must preserve its chat parent identity",
        )
        self.soft_assert_equal(
            json.loads(tool_result).get("status"),
            "unavailable",
            "The tool should preserve the shared manual result contract",
        )
        self.soft_assert(
            len(author_tasks) == 2
            and all(task.parent_task_id in history_ids for task in author_tasks),
            "Each map-authoring task should be nested under its compaction task",
        )
        checkpoints = store.list_context_checkpoints(session_id, vault.name)
        self.soft_assert_equal(
            [checkpoint.checkpoint_kind for checkpoint in checkpoints],
            ["session_map", "session_map"],
            "Manual compaction should create successive inspectable map revisions",
        )
        await self._check_shared_author_and_no_op_contract(vault.name, str(vault))
        await self._check_compaction_activity_contract(vault.name)
        self.assert_no_failures()

    async def _check_compaction_activity_contract(self, vault_name: str) -> None:
        import core.chat.compaction as compaction

        runtime = get_runtime_context()
        store = runtime.chat_store
        response = self.call_api(
            "/api/system/settings/general/compaction_strategy",
            method="PUT",
            data={"value": "recovery_card"},
        )
        assert response.status_code == 200
        private_marker = "PRIVATE_COMPACTION_FAILURE_CONTENT"
        session_ids = ("activity-v1-failure-first", "activity-v1-failure-second")
        for session_id in session_ids:
            store.ensure_session(
                session_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
            )
            store.add_messages(
                session_id,
                vault_name,
                [
                    _user("Older"),
                    _assistant("Answer"),
                    _user("Recent"),
                    _assistant("Answer"),
                ],
            )
            with patch(
                "core.chat.compaction._generate_compaction_summary",
                AsyncMock(side_effect=RuntimeError(private_marker + "x" * 8000)),
            ):
                try:
                    await compaction.run_chat_context_compaction(
                        session_id=session_id,
                        vault_name=vault_name,
                        authority=ExecutionAuthority(LOCAL_USER_PRINCIPAL_ID),
                        store=store,
                    )
                except RuntimeError:
                    pass
                else:
                    self.soft_assert(
                        False, "An author failure must remain a real task failure"
                    )

        activity_response = self.call_api("/api/system/activity-log?limit=200")
        assert activity_response.status_code == 200
        entries = [
            entry["data"]
            for entry in activity_response.json()["entries"]
            if entry.get("data", {}).get("event", "").startswith("chat_compaction_")
            and entry["data"].get("session_id") in session_ids
        ]
        failures = [
            entry for entry in entries if entry["event"] == "chat_compaction_failed"
        ]
        self.soft_assert_equal(
            {entry.get("session_id") for entry in failures},
            set(session_ids),
            "Distinct-session compaction failures must not collapse under warning deduplication",
        )
        self.soft_assert_equal(
            len({entry.get("issue") for entry in failures}),
            2,
            "Each failed compaction operation needs a stable distinct issue identity",
        )
        for session_id in session_ids:
            tasks = await runtime.task_coordinator.list_tasks(
                kind=ExecutionTaskKind.HISTORY_COMPACTION.value,
                scope=chat_session_scope(session_id),
            )
            assert len(tasks) == 1
            lifecycle = {
                entry["event"]: entry
                for entry in entries
                if entry["session_id"] == session_id
            }
            self.soft_assert_equal(
                {event: entry.get("status") for event, entry in lifecycle.items()},
                {
                    "chat_compaction_started": "started",
                    "chat_compaction_plan_selected": "selected",
                    "chat_compaction_failed": "failed",
                },
                "V1 Activity must expose an explicit start/plan/failure lifecycle",
            )
            self.soft_assert(
                all(
                    entry.get("task_id") == tasks[0].task_id
                    for entry in lifecycle.values()
                ),
                "Every V1 lifecycle event must identify its governed outer task",
            )
            self.soft_assert(
                all(
                    entry.get("vault_name") == vault_name
                    for entry in lifecycle.values()
                ),
                "Every V1 lifecycle event must retain its vault identity",
            )
        self.soft_assert(
            private_marker not in json.dumps(entries),
            "Compaction Activity must not expose exception contents",
        )
        self.soft_assert(
            all(
                entry.get("reason") == "authoring_failed"
                and entry.get("error_type") == "RuntimeError"
                and len(str(entry.get("error", ""))) <= 200
                for entry in failures
            ),
            "Failures should project bounded content-safe stage and error diagnostics",
        )

    async def _check_shared_author_and_no_op_contract(
        self, vault_name: str, vault_path: str
    ) -> None:
        import core.chat.compaction as compaction

        runtime = get_runtime_context()
        store = runtime.chat_store
        authority = ExecutionAuthority(LOCAL_USER_PRINCIPAL_ID)
        for strategy in ("recovery_card", "session_map"):
            response = self.call_api(
                "/api/system/settings/general/compaction_strategy",
                method="PUT",
                data={"value": strategy},
            )
            assert response.status_code == 200
            for message_count in (0, 2):
                session_id = f"manual-no-op-{strategy}-{message_count}"
                store.ensure_session(
                    session_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
                )
                if message_count:
                    store.add_messages(
                        session_id,
                        vault_name,
                        [_user("Only turn"), _assistant("Answer")],
                    )
                revision = store.get_session_history_revision(session_id, vault_name)
                response = self.call_api(
                    f"/api/chat/sessions/{session_id}/compact",
                    method="POST",
                    data={"vault_name": vault_name},
                )
                payload = response.json()
                self.soft_assert_equal(
                    (
                        response.status_code,
                        payload.get("status"),
                        payload.get("reason"),
                    ),
                    (200, "unavailable", "retained_turn_floor"),
                    f"{strategy} should treat an empty or retained-only request as a no-op",
                )
                self.soft_assert_equal(
                    (payload.get("messages_before"), payload.get("messages_after")),
                    (message_count, message_count),
                    "A no-op should report unchanged effective history",
                )
                self.soft_assert_equal(
                    store.get_session_history_revision(session_id, vault_name),
                    revision,
                    "A no-op must not advance the durable history revision",
                )
                self.soft_assert_equal(
                    store.list_context_checkpoints(session_id, vault_name),
                    [],
                    "A no-op must not create a checkpoint or pin a strategy",
                )
                tasks = await runtime.task_coordinator.list_tasks(
                    kind=ExecutionTaskKind.HISTORY_COMPACTION.value,
                    scope=chat_session_scope(session_id),
                )
                self.soft_assert(
                    len(tasks) == 1
                    and tasks[0].status == "completed"
                    and tasks[0].result is not None
                    and tasks[0].result.get("status") == "unavailable",
                    "An explicit no-op must complete its governed task with its result",
                )
                activity_response = self.call_api("/api/system/activity-log?limit=200")
                assert activity_response.status_code == 200
                self.soft_assert(
                    any(
                        entry.get("data", {}).get("event")
                        == "chat_compaction_unavailable"
                        and entry["data"].get("session_id") == session_id
                        and entry["data"].get("vault_name") == vault_name
                        and entry["data"].get("strategy") == strategy
                        and entry["data"].get("status") == "unavailable"
                        and entry["data"].get("reason") == "retained_turn_floor"
                        and entry["data"].get("task_id") == tasks[0].task_id
                        for entry in activity_response.json()["entries"]
                    ),
                    "System Activity must distinguish a harmless no-op from a failed compaction",
                )

            session_id = f"manual-author-readiness-{strategy}"
            store.ensure_session(
                session_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
            )
            store.add_messages(
                session_id,
                vault_name,
                [
                    _user("Older"),
                    _assistant("Older answer"),
                    _user("New"),
                    _assistant("New answer"),
                ],
            )
            revision = store.get_session_history_revision(session_id, vault_name)
            for model, thinking, availability_error, expected_reason in (
                ("jev", "low", None, "author_model_not_text_capable"),
                ("unknown-author", "low", None, "author_model_unknown"),
                ("test", "impossible", None, "author_thinking_invalid"),
                (
                    "gpt",
                    "low",
                    ValueError("No usable auth"),
                    "author_model_unavailable",
                ),
            ):
                rejected_author = AsyncMock(
                    side_effect=AssertionError("Unready author invoked")
                )
                with (
                    patch(
                        "core.memory.session_map.readiness.get_compaction_author_model",
                        return_value=model,
                    ),
                    patch(
                        "core.chat.compaction.get_compaction_author_model",
                        return_value=model,
                    ),
                    patch(
                        "core.memory.session_map.readiness.get_compaction_author_thinking",
                        side_effect=(
                            ValueError("Bad thinking")
                            if thinking == "impossible"
                            else None
                        ),
                        return_value=thinking,
                    ),
                    patch(
                        "core.memory.session_map.readiness.validate_api_keys",
                        side_effect=availability_error,
                    ),
                    patch(
                        "core.chat.compaction._generate_compaction_summary",
                        rejected_author,
                    ),
                    patch(
                        "core.memory.session_map.service._invoke_session_map_model",
                        rejected_author,
                    ),
                ):
                    error: Exception | None = None
                    try:
                        await compaction.compact_chat_context(
                            session_id=session_id,
                            vault_name=vault_name,
                            vault_path=vault_path,
                            authority=authority,
                            store=store,
                        )
                    except Exception as exc:
                        error = exc
                    self.soft_assert(
                        isinstance(error, ValueError) and expected_reason in str(error),
                        f"{strategy} must reject {expected_reason} before inference",
                    )
                    self.soft_assert_equal(
                        rejected_author.await_count, 0, "An unready author must not run"
                    )
                self.soft_assert_equal(
                    store.get_session_history_revision(session_id, vault_name),
                    revision,
                    "Rejected author readiness must not change session state",
                )


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
