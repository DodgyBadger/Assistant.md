"""Validate explicit single-session Compaction v1 to v2 upgrades."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.chat.chat_store import ChatStore  # noqa: E402
from core.chat.compaction import build_compaction_summary_message  # noqa: E402
from core.chat.context_strategy_upgrade import (  # noqa: E402
    SessionContextStrategyUpgradeUnavailable,
    get_session_context_strategy_status,
    start_session_context_strategy_upgrade,
)
from core.identity import LOCAL_USER_AUTHORITY, LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.checkpoints import (
    load_session_map_checkpoint,  # noqa: E402
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapTrajectory,
    SourceRange,
)
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    chat_session_scope,
    get_current_execution_task,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from core.runtime.task_runner import (  # noqa: E402
    ExecutionGatePolicy,
    ExecutionTaskSpec,
)
from validation.core.base_scenario import BaseScenario  # noqa: E402
from validation.core.tool_history_fixtures import tool_reply_cases  # noqa: E402


class SessionContextStrategyUpgradeScenario(BaseScenario):
    """Upgrade only an explicitly selected V1 session without losing history."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SessionContextStrategyUpgradeVault")
        await self.start_system()
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("default_model", "test"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "250"),
            ("compaction_retained_turns", "2"),
            ("compaction_high_watermark_tokens", "500"),
            ("compaction_type", "auto"),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200, f"{key} setting should update"

        runtime = get_runtime_context()
        store = runtime.chat_store
        unpinned_session_id = "strategy-upgrade-unpinned"
        store.ensure_session(
            unpinned_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            unpinned_session_id,
            vault.name,
            [_user("Short question"), _assistant("Short answer")],
        )

        session_id = "strategy-upgrade-v1"
        raw_messages = _long_conversation("primary", pair_count=10)
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(session_id, vault.name, raw_messages[:6])
        _add_recovery_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            checkpoint_id="recovery-one",
            boundary=5,
        )
        store.add_messages(session_id, vault.name, raw_messages[6:12])
        _add_recovery_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            checkpoint_id="recovery-two",
            boundary=11,
        )
        store.add_messages(session_id, vault.name, raw_messages[12:])
        raw_before = store.get_history(session_id, vault.name, mode="raw")

        with patch(
            "api.services.chat_sessions._chat_store.get_stored_messages",
            side_effect=AssertionError(
                "Session listing must not hydrate canonical message content"
            ),
        ):
            sessions_response = self.call_api(
                f"/api/chat/sessions?vault_name={vault.name}"
            )
        assert sessions_response.status_code == 200
        listed = {item["session_id"]: item for item in sessions_response.json()}
        self.soft_assert_equal(
            (
                listed[unpinned_session_id]["context_strategy"],
                listed[unpinned_session_id]["can_upgrade_to_v2"],
            ),
            ("unassigned", False),
            "An unpinned session should not be presented as a migration candidate",
        )
        self.soft_assert_equal(
            (
                listed[session_id]["context_strategy"],
                listed[session_id]["can_upgrade_to_v2"],
            ),
            ("recovery_card", True),
            "A V1 session should expose the explicit upgrade action",
        )
        with patch(
            "core.memory.session_map.readiness.model_supports_capability",
            return_value=False,
        ):
            unavailable_status = get_session_context_strategy_status(
                store=store, session_id=session_id, vault_name=vault.name
            )
            self.soft_assert_equal(
                (unavailable_status.can_upgrade_to_v2, unavailable_status.reason),
                (False, "author_model_not_text_capable"),
                "Upgrade status must use the V2 author capability rule",
            )
            try:
                await start_session_context_strategy_upgrade(
                    session_id=session_id,
                    vault_name=vault.name,
                    authority=LOCAL_USER_AUTHORITY,
                )
            except SessionContextStrategyUpgradeUnavailable as exc:
                self.soft_assert_equal(
                    exc.reason,
                    "author_model_not_text_capable",
                    "Upgrade admission must share the status readiness reason",
                )
            else:
                raise AssertionError("Decision-only authors cannot start an upgrade")
        unavailable_activity = self.call_api("/api/system/activity-log?limit=200")
        assert unavailable_activity.status_code == 200
        unavailable_rows = [
            entry["data"]
            for entry in unavailable_activity.json()["entries"]
            if entry.get("tag") == "context-strategy-upgrade"
            and entry.get("data", {}).get("event")
            == "context_strategy_upgrade_unavailable"
            and entry.get("data", {}).get("session_id") == session_id
        ]
        self.soft_assert(
            any(
                row.get("status") == "unavailable"
                and row.get("vault_name") == vault.name
                and row.get("source") == "api"
                and row.get("reason") == "author_model_not_text_capable"
                and row.get("operation_id")
                for row in unavailable_rows
            ),
            "Rejected upgrade admission should retain safe domain identity and reason",
        )

        authored_ranges: list[tuple[int, int]] = []
        author_parent_tasks: list[str | None] = []
        author_started = asyncio.Event()
        release_author = asyncio.Event()

        async def author_cumulative_map(**kwargs: object) -> SessionMapDraft:
            if not author_started.is_set():
                author_started.set()
                await release_author.wait()
            prompt = json.loads(str(kwargs["prompt"]))
            new_ranges = [
                item["source_range"] for item in prompt["new_evidence_envelopes"]
            ]
            newest_end = int(new_ranges[-1]["end"])
            previous = prompt["previous_map"]
            previous_trajectory = previous.get("trajectory")
            start = (
                int(previous_trajectory["sources"][0]["start"])
                if previous_trajectory
                else int(new_ranges[0]["start"])
            )
            authored_ranges.append((start, newest_end))
            task = get_current_execution_task()
            author_parent_tasks.append(task.parent_task_id if task else None)
            return SessionMapDraft(
                trajectory=SessionMapTrajectory(
                    text="The selected session preserves its cumulative working narrative.",
                    sources=(SourceRange(start=start, end=newest_end),),
                )
            )

        event_checkpoint = self.event_checkpoint()
        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=author_cumulative_map,
        ):
            start_response = self.call_api(
                f"/api/chat/sessions/{session_id}/upgrade-context-strategy",
                method="POST",
                data={"vault_name": vault.name},
            )
            self.soft_assert_equal(
                start_response.status_code,
                202,
                "The upgrade endpoint should return after starting background work",
            )
            task_id = start_response.json()["task"]["task_id"]
            await asyncio.wait_for(author_started.wait(), timeout=2)
            duplicate = self.call_api(
                f"/api/chat/sessions/{session_id}/upgrade-context-strategy",
                method="POST",
                data={"vault_name": vault.name},
            )
            self.soft_assert_equal(
                duplicate.status_code, 202, "An active upgrade is reusable"
            )
            self.soft_assert_equal(
                duplicate.json()["task"]["task_id"],
                task_id,
                "Repeated upgrade requests must reuse the active task",
            )
            active_chat_response = self.call_api(
                f"/api/chat/sessions/{session_id}/active-task"
            )
            self.soft_assert_equal(
                active_chat_response.status_code,
                404,
                "A strategy upgrade must not be exposed as an active chat response",
            )
            release_author.set()
            terminal = await _wait_for_terminal_task(task_id)

        self.soft_assert_equal(
            terminal.status if terminal else None,
            "completed",
            "The selected V1 upgrade should complete as one governed task",
        )
        self.soft_assert(
            len(authored_ranges) >= 2,
            "Historical recovery boundaries should produce bounded authoring passes",
        )
        self.soft_assert_equal(
            author_parent_tasks,
            [task_id] * len(author_parent_tasks),
            "Every map-author pass should be a child of the upgrade task",
        )
        latest = store.get_latest_context_checkpoint(session_id, vault.name)
        assert latest is not None
        self.soft_assert_equal(
            latest.checkpoint_kind,
            "session_map",
            "The completed operation should make Compaction v2 effective",
        )
        self.soft_assert_equal(
            store.get_history(session_id, vault.name, mode="raw"),
            raw_before,
            "Strategy upgrade must preserve the canonical raw transcript",
        )
        self.soft_assert_equal(
            len(
                store.list_context_checkpoints(
                    session_id,
                    vault.name,
                    checkpoint_kind="recovery_card",
                )
            ),
            2,
            "Historical recovery-card checkpoints should remain inspectable",
        )
        self.soft_assert_equal(
            len(
                store.list_context_checkpoints(
                    session_id,
                    vault.name,
                    checkpoint_kind="session_map",
                )
            ),
            1,
            "A successful upgrade should append exactly one V2 checkpoint",
        )
        upgraded_map = load_session_map_checkpoint(latest)
        self.soft_assert(
            upgraded_map.trajectory is not None,
            "The final V2 checkpoint should retain the staged authored map",
        )
        map_response = self.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name={vault.name}"
        )
        self.soft_assert_equal(
            map_response.status_code,
            200,
            "The upgraded map should remain available through normal inspection",
        )
        upgraded_sessions = self.call_api(
            f"/api/chat/sessions?vault_name={vault.name}"
        ).json()
        upgraded_listing = next(
            item for item in upgraded_sessions if item["session_id"] == session_id
        )
        self.soft_assert_equal(
            (
                upgraded_listing["context_strategy"],
                upgraded_listing["can_upgrade_to_v2"],
                upgraded_listing["has_session_map"],
            ),
            ("session_map", False, True),
            "The completed session should lose the upgrade action and gain its map affordance",
        )
        repeated = self.call_api(
            f"/api/chat/sessions/{session_id}/upgrade-context-strategy",
            method="POST",
            data={"vault_name": vault.name},
        )
        self.soft_assert_equal(
            repeated.status_code,
            409,
            "An existing V2 session should reject repeated migration",
        )

        events = [event.get("name") for event in self.events_since(event_checkpoint)]
        for event_name in (
            "context_strategy_upgrade_started",
            "context_strategy_upgrade_planned",
            "context_strategy_upgrade_completed",
        ):
            self.soft_assert(
                event_name in events,
                f"The upgrade should emit {event_name} observability",
            )
        activity = self.call_api("/api/system/activity-log?limit=200")
        assert activity.status_code == 200
        upgrade_rows = [
            entry["data"]
            for entry in activity.json()["entries"]
            if entry.get("tag") == "context-strategy-upgrade"
            and entry.get("data", {}).get("task_id") == task_id
        ]
        assert (
            len(upgrade_rows) == 3
        ), "Upgrade Activity has one start, plan, and terminal row"
        assert {row["status"] for row in upgrade_rows} == {
            "running",
            "planned",
            "completed",
        }
        assert all(
            row["source"] == "api" and row["parent_task_id"] is None
            for row in upgrade_rows
        )
        assert not any(
            entry.get("tag") == "session-map-authoring"
            and entry.get("data", {}).get("parent_task_id") == task_id
            for entry in activity.json()["entries"]
        ), "Successful reconstruction passes should not add domain Activity noise"
        pass_starts = self.find_events(
            self.events_since(event_checkpoint),
            name="session_map_authoring_started",
            parent_task_id=task_id,
            status="running",
        )
        pass_completions = self.find_events(
            self.events_since(event_checkpoint),
            name="session_map_authoring_completed",
            parent_task_id=task_id,
            status="completed",
        )
        assert len(pass_starts) == len(pass_completions) == len(authored_ranges)

        failure_session_id = "strategy-upgrade-failure"
        failure_messages = _long_conversation("failure", pair_count=6)
        store.ensure_session(
            failure_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(failure_session_id, vault.name, failure_messages)
        _add_recovery_checkpoint(
            store=store,
            session_id=failure_session_id,
            vault_name=vault.name,
            checkpoint_id="failure-recovery",
            boundary=5,
        )

        async def fail_authoring(**_kwargs: object) -> SessionMapDraft:
            raise RuntimeError("deterministic upgrade author failure")

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=fail_authoring,
        ):
            failed_start = self.call_api(
                f"/api/chat/sessions/{failure_session_id}/upgrade-context-strategy",
                method="POST",
                data={"vault_name": vault.name},
            )
            assert failed_start.status_code == 202
            failed_task = await _wait_for_terminal_task(
                failed_start.json()["task"]["task_id"]
            )

        self.soft_assert_equal(
            failed_task.status if failed_task else None,
            "failed",
            "An authoring error should fail the owning upgrade task",
        )
        failed_latest = store.get_latest_context_checkpoint(
            failure_session_id, vault.name
        )
        self.soft_assert_equal(
            failed_latest.checkpoint_kind if failed_latest else None,
            "recovery_card",
            "A failed staged upgrade must leave V1 effective",
        )
        self.soft_assert_equal(
            store.list_context_checkpoints(
                failure_session_id,
                vault.name,
                checkpoint_kind="session_map",
            ),
            [],
            "A failed upgrade must not append a partial V2 checkpoint",
        )

        author_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        self.soft_assert(
            any(task.parent_task_id == task_id for task in author_tasks),
            "The normal session-map author task path should remain observable",
        )
        await self._test_stale_authoring(vault.name)
        await self._test_upgrade_plan_readiness(vault.name)
        await self._test_session_gate(vault.name)
        await self._test_manual_compaction_contention(vault.name)
        await self._test_failure_logging(vault.name)
        await self._test_cancellation_and_restart(vault.name)
        self.assert_no_failures()

    async def _test_upgrade_plan_readiness(self, vault_name: str) -> None:
        runtime = get_runtime_context()
        store = runtime.chat_store

        async def reply_author(**kwargs: object) -> SessionMapDraft:
            return _cumulative_draft(kwargs)

        for case in tool_reply_cases():
            reply_session_id = f"strategy-upgrade-tool-reply-{case.name}"
            _seed_v1(store, reply_session_id, vault_name)
            store.add_messages(reply_session_id, vault_name, case.messages)
            status = get_session_context_strategy_status(
                store=store, session_id=reply_session_id, vault_name=vault_name
            )
            self.soft_assert_equal(
                status.can_upgrade_to_v2,
                not case.issue_codes,
                f"Upgrade readiness must honor {case.name} invocation matching",
            )
            if case.issue_codes:
                rejected = self.call_api(
                    f"/api/chat/sessions/{reply_session_id}/upgrade-context-strategy",
                    method="POST",
                    data={"vault_name": vault_name},
                )
                self.soft_assert_equal(
                    (
                        rejected.status_code,
                        rejected.json().get("details", {}).get("reason"),
                    ),
                    (409, "upgrade_plan_invalid_tool_history"),
                    f"Upgrade admission must reject {case.name}",
                )
                tasks = await runtime.task_coordinator.list_tasks(
                    kind=ExecutionTaskKind.CONTEXT_STRATEGY_UPGRADE,
                    scope=chat_session_scope(reply_session_id),
                )
                self.soft_assert_equal(
                    tasks, [], f"Rejected {case.name} must not create a task"
                )
            else:
                raw_before = store.get_history(reply_session_id, vault_name, mode="raw")
                with patch(
                    "core.memory.session_map.service._invoke_session_map_model",
                    new=reply_author,
                ):
                    task = await _start_upgrade(reply_session_id, vault_name)
                    terminal = await _wait_for_terminal_task(task.task_id)
                self.soft_assert_equal(
                    terminal.status, "completed", f"Upgrade must commit {case.name}"
                )
                self.soft_assert_equal(
                    store.get_history(reply_session_id, vault_name, mode="raw"),
                    raw_before,
                    "Tool-reply upgrades must preserve canonical history",
                )
        session_id = "strategy-upgrade-retained-setting"
        _seed_v1(store, session_id, vault_name)
        for floor in (10, 2):
            response = self.call_api(
                "/api/system/settings/general/compaction_retained_turns",
                method="PUT",
                data={"value": str(floor)},
            )
            assert response.status_code == 200
            with (
                patch.object(
                    store, "get_stored_messages", wraps=store.get_stored_messages
                ) as hydrate,
                patch.object(
                    store,
                    "get_raw_history_structure",
                    wraps=store.get_raw_history_structure,
                ) as inspect_structure,
                patch(
                    "core.chat.compaction.plan_stepped_history_eviction",
                    side_effect=AssertionError(
                        "Status must not select a full eviction boundary"
                    ),
                ),
            ):
                status = get_session_context_strategy_status(
                    store=store, session_id=session_id, vault_name=vault_name
                )
                self.soft_assert_equal(
                    hydrate.call_count,
                    0,
                    "Upgrade status should not hydrate canonical message content",
                )
                self.soft_assert_equal(
                    inspect_structure.call_count,
                    1,
                    "Upgrade status should inspect canonical history structure once",
                )
            self.soft_assert_equal(
                status.can_upgrade_to_v2,
                True,
                "V1 retained turns must not block V2 upgrade eligibility",
            )
            forked = self.call_api(
                f"/api/chat/sessions/{session_id}/fork",
                method="POST",
                data={"vault_name": vault_name, "through_sequence_index": 11},
            )
            assert forked.status_code == 200
            child = forked.json()["session"]
            self.soft_assert_equal(
                (child["context_strategy"], child["can_upgrade_to_v2"]),
                ("recovery_card", True),
                "A V1 fork must inherit truthful upgrade eligibility",
            )
            listed = self.call_api(f"/api/chat/sessions?vault_name={vault_name}")
            assert listed.status_code == 200
            rows = {row["session_id"]: row for row in listed.json()}
            self.soft_assert(
                rows[session_id]["can_upgrade_to_v2"]
                and rows[child["session_id"]]["can_upgrade_to_v2"],
                "Session listing must agree with source and fork upgrade readiness",
            )

        assert (
            self.call_api(
                "/api/system/settings/general/compaction_retained_turns",
                method="PUT",
                data={"value": "10"},
            ).status_code
            == 200
        )
        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=reply_author,
        ):
            upgraded_child = await _start_upgrade(child["session_id"], vault_name)
            terminal = await _wait_for_terminal_task(upgraded_child.task_id)
        assert terminal.status == "completed"

        for label, messages, reason in (
            ("empty", [], "canonical_history_empty"),
            (
                "incomplete",
                [_user("Unanswered")],
                "upgrade_plan_incomplete_latest_group",
            ),
            ("complete", [_user("Question"), _assistant("Answer")], "ready"),
            (
                "incomplete-tail",
                [_user("Question"), _assistant("Answer"), _user("Unanswered")],
                "ready",
            ),
        ):
            shape_id = f"strategy-upgrade-shape-{label}"
            store.ensure_session(
                shape_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
            )
            if messages:
                store.add_messages(shape_id, vault_name, messages)
            _add_recovery_checkpoint(
                store=store,
                session_id=shape_id,
                vault_name=vault_name,
                checkpoint_id=f"{shape_id}-recovery",
                boundary=len(messages) - 1,
            )
            status = get_session_context_strategy_status(
                store=store, session_id=shape_id, vault_name=vault_name
            )
            assert (status.can_upgrade_to_v2, status.reason) == (
                reason == "ready",
                reason,
            )
            if reason != "ready":
                rejected = self.call_api(
                    f"/api/chat/sessions/{shape_id}/upgrade-context-strategy",
                    method="POST",
                    data={"vault_name": vault_name},
                )
                assert (
                    rejected.status_code == 409
                    and rejected.json()["details"]["reason"] == reason
                )
                assert not await runtime.task_coordinator.list_tasks(
                    kind=ExecutionTaskKind.CONTEXT_STRATEGY_UPGRADE,
                    scope=chat_session_scope(shape_id),
                )
                continue
            raw_before = store.get_history(shape_id, vault_name, mode="raw")
            with patch(
                "core.memory.session_map.service._invoke_session_map_model",
                new=reply_author,
            ):
                task = await _start_upgrade(shape_id, vault_name)
                terminal = await _wait_for_terminal_task(task.task_id)
            assert terminal.status == "completed"
            assert store.get_history(shape_id, vault_name, mode="raw") == raw_before
            if label == "incomplete-tail":
                assert store.get_history(shape_id, vault_name)[-1] == messages[-1]

        store.add_messages(
            session_id,
            vault_name,
            [
                _user("Use a tool"),
                ModelResponse(
                    parts=[
                        ToolCallPart(tool_name="probe", args={}, tool_call_id="orphan")
                    ]
                ),
                _user("Continue without its return"),
                _assistant("Continue"),
            ],
        )
        status = get_session_context_strategy_status(
            store=store, session_id=session_id, vault_name=vault_name
        )
        self.soft_assert_equal(
            (status.can_upgrade_to_v2, status.reason),
            (False, "upgrade_plan_invalid_tool_history"),
            "Malformed canonical tool history must block an upgrade",
        )
        try:
            await _start_upgrade(session_id, vault_name)
        except SessionContextStrategyUpgradeUnavailable as exc:
            self.soft_assert_equal(
                exc.reason,
                status.reason,
                "Upgrade admission must share the tool-history readiness reason",
            )
        else:
            raise AssertionError(
                "Malformed canonical history must reject upgrade admission"
            )
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.CONTEXT_STRATEGY_UPGRADE,
            scope=chat_session_scope(session_id),
        )
        self.soft_assert_equal(
            tasks, [], "Malformed-history rejection must not create a task"
        )
        for label, tool_call_id, return_name in (
            ("blank", "", "probe"),
            ("whitespace", " \t ", "probe"),
            ("name-mismatch", "id", "other"),
        ):
            identity_session_id = f"strategy-upgrade-invalid-identity-{label}"
            _seed_v1(store, identity_session_id, vault_name)
            store.add_messages(
                identity_session_id,
                vault_name,
                [
                    _user("Use a tool"),
                    ModelResponse(
                        parts=[
                            ToolCallPart(
                                tool_name="probe", args={}, tool_call_id=tool_call_id
                            )
                        ]
                    ),
                    ModelRequest(
                        parts=[
                            ToolReturnPart(
                                tool_name=return_name,
                                content="result",
                                tool_call_id=tool_call_id,
                            )
                        ]
                    ),
                    _assistant("Completed"),
                ],
            )
            status = get_session_context_strategy_status(
                store=store, session_id=identity_session_id, vault_name=vault_name
            )
            self.soft_assert_equal(
                (status.can_upgrade_to_v2, status.reason),
                (False, "upgrade_plan_invalid_tool_history"),
                f"Upgrade readiness must reject {label} tool identity",
            )
            rejected = self.call_api(
                f"/api/chat/sessions/{identity_session_id}/upgrade-context-strategy",
                method="POST",
                data={"vault_name": vault_name},
            )
            self.soft_assert_equal(
                (
                    rejected.status_code,
                    rejected.json().get("details", {}).get("reason"),
                ),
                (409, "upgrade_plan_invalid_tool_history"),
                f"Upgrade admission must reject {label} tool identity",
            )
            tasks = await runtime.task_coordinator.list_tasks(
                kind=ExecutionTaskKind.CONTEXT_STRATEGY_UPGRADE,
                scope=chat_session_scope(identity_session_id),
            )
            self.soft_assert_equal(
                tasks, [], "Invalid tool identity must not create an upgrade task"
            )

    async def _test_stale_authoring(self, vault_name: str) -> None:
        runtime = get_runtime_context()
        store = runtime.chat_store
        other_store = ChatStore(system_root=str(runtime.config.system_root))
        for mutation in ("append", "delete"):
            session_id = f"strategy-upgrade-stale-{mutation}"
            _seed_v1(store, session_id, vault_name)
            raw_before = store.get_history(session_id, vault_name, mode="raw") or []
            effective_before = store.get_history(session_id, vault_name) or []
            started = asyncio.Event()
            release = asyncio.Event()
            author_calls: list[object] = []

            async def blocked_author(
                *,
                started: asyncio.Event = started,
                release: asyncio.Event = release,
                author_calls: list[object] = author_calls,
                **kwargs: object,
            ) -> SessionMapDraft:
                author_calls.append(kwargs)
                started.set()
                await release.wait()
                return _cumulative_draft(kwargs)

            with patch(
                "core.memory.session_map.service._invoke_session_map_model",
                new=blocked_author,
            ):
                task = await _start_upgrade(session_id, vault_name)
                await asyncio.wait_for(started.wait(), timeout=2)
                appended = [
                    _user("Concurrent update"),
                    _assistant("Preserve this update"),
                ]
                if mutation == "append":
                    other_store.add_messages(session_id, vault_name, appended)
                else:
                    other_store.delete_sessions(vault_name, session_id=session_id)
                release.set()
                terminal = await _wait_for_terminal_task(task.task_id)

            assert terminal is not None and terminal.status == "failed"
            assert terminal.result is None
            assert (
                len(author_calls) == 1
            ), "Stale evidence must abort before another pass"
            assert (
                store.list_context_checkpoints(
                    session_id, vault_name, checkpoint_kind="session_map"
                )
                == []
            )
            if mutation == "append":
                assert (
                    terminal.terminal_error_type
                    == "SessionContextStrategyUpgradeUnavailable"
                )
                assert (
                    store.get_history(session_id, vault_name, mode="raw")
                    == raw_before + appended
                )
                assert (
                    store.get_history(session_id, vault_name)
                    == effective_before + appended
                )
            else:
                assert store.get_session(session_id, vault_name) is None

    async def _test_session_gate(self, vault_name: str) -> None:
        """A queued upgrade observes the completed preceding chat's checkpoint."""
        runtime = get_runtime_context()
        store = runtime.chat_store
        session_id = "strategy-upgrade-queued"
        _seed_v1(store, session_id, vault_name)
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def forbidden_author(**_kwargs: object) -> SessionMapDraft:
            nonlocal calls
            calls += 1
            raise AssertionError("A stale queued upgrade must not spend inference")

        async def preceding_chat(task: ExecutionTaskSnapshot) -> None:
            async def gated_chat() -> None:
                entered.set()
                await release.wait()
                _add_recovery_checkpoint(
                    store=store,
                    session_id=session_id,
                    vault_name=vault_name,
                    checkpoint_id="queued-new-recovery",
                    boundary=7,
                )

            await runtime.task_runner.run_with_gate(
                task,
                ExecutionGatePolicy(
                    key=chat_session_scope(session_id), queued_status="queued"
                ),
                gated_chat,
            )

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=forbidden_author,
        ):
            chat = await runtime.task_runner.start_background(
                ExecutionTaskSpec(
                    kind=ExecutionTaskKind.CHAT,
                    scope=chat_session_scope(session_id),
                    source=ExecutionTaskSource.API,
                    label="Preceding deterministic chat checkpoint",
                    authority=LOCAL_USER_AUTHORITY,
                ),
                preceding_chat,
            )
            await asyncio.wait_for(entered.wait(), timeout=2)
            upgrade = await _start_upgrade(session_id, vault_name)
            queued = await _wait_for_queued_task(upgrade.task_id)
            assert queued.metadata["waiting_for_task_id"] == chat.task_id
            await runtime.task_coordinator.cancel_task(upgrade.task_id)
            cancelled = await _wait_for_terminal_task(upgrade.task_id)
            assert cancelled.status == "cancelled"
            assert (
                await runtime.task_coordinator.list_child_tasks(upgrade.task_id) == []
            )
            upgrade = await _start_upgrade(session_id, vault_name)
            queued = await _wait_for_queued_task(upgrade.task_id)
            assert queued.metadata["waiting_for_task_id"] == chat.task_id
            duplicate = await _start_upgrade(session_id, vault_name)
            assert duplicate.task_id == upgrade.task_id
            release.set()
            assert (await _wait_for_terminal_task(chat.task_id)).status == "completed"
            terminal = await _wait_for_terminal_task(upgrade.task_id)

        assert terminal is not None and terminal.status == "failed"
        assert (
            terminal.terminal_error_type == "SessionContextStrategyUpgradeUnavailable"
        )
        assert calls == 0
        assert (
            store.get_latest_context_checkpoint(session_id, vault_name).checkpoint_id
            == "queued-new-recovery"
        )

    async def _test_manual_compaction_contention(self, vault_name: str) -> None:
        """Manual compaction re-resolves pinning after waiting for an upgrade."""
        import core.chat.compaction as compaction

        runtime = get_runtime_context()
        store = runtime.chat_store
        session_id = "strategy-upgrade-manual-contention"
        _seed_v1(store, session_id, vault_name)
        raw_before = store.get_history(session_id, vault_name, mode="raw")
        started = asyncio.Event()
        release = asyncio.Event()
        v1_entered = asyncio.Event()
        original_v1 = compaction.compact_chat_history

        async def blocked_author(**kwargs: object) -> SessionMapDraft:
            started.set()
            await release.wait()
            return _cumulative_draft(kwargs)

        async def record_v1_admission(**kwargs: object):
            v1_entered.set()
            return await original_v1(**kwargs)

        async def forbidden_v1_author(**_kwargs: object) -> str:
            raise AssertionError("V1 must not author after V2 becomes pinned")

        with (
            patch(
                "core.memory.session_map.service._invoke_session_map_model",
                new=blocked_author,
            ),
            patch.object(compaction, "compact_chat_history", new=record_v1_admission),
            patch.object(
                compaction, "_generate_compaction_summary", new=forbidden_v1_author
            ),
        ):
            upgrade = await _start_upgrade(session_id, vault_name)
            await asyncio.wait_for(started.wait(), timeout=2)
            manual_request = asyncio.create_task(
                compaction.run_chat_context_compaction(
                    session_id=session_id,
                    vault_name=vault_name,
                    authority=LOCAL_USER_AUTHORITY,
                    source=ExecutionTaskSource.API,
                    store=store,
                )
            )
            try:
                await asyncio.wait_for(v1_entered.wait(), timeout=2)
                manual_tasks = await runtime.task_coordinator.list_tasks(
                    kind=ExecutionTaskKind.HISTORY_COMPACTION,
                    scope=chat_session_scope(session_id),
                )
                assert len(manual_tasks) == 1 and not manual_tasks[0].is_terminal
                assert not manual_request.done()
            finally:
                release.set()
            response = await asyncio.wait_for(manual_request, timeout=5)
            completed = await _wait_for_terminal_task(upgrade.task_id)

        assert completed.status == "completed"
        assert response.as_api_dict()["strategy"] == "session_map"
        manual_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION,
            scope=chat_session_scope(session_id),
        )
        assert len(manual_tasks) == 1
        assert manual_tasks[0].status == "completed"
        assert manual_tasks[0].source == ExecutionTaskSource.API
        assert (
            manual_tasks[0].result
            and manual_tasks[0].result["strategy"] == "session_map"
        )
        assert store.get_history(session_id, vault_name, mode="raw") == raw_before
        assert (
            store.get_latest_context_checkpoint(session_id, vault_name).checkpoint_kind
            == "session_map"
        )
        assert (
            len(
                store.list_context_checkpoints(
                    session_id, vault_name, checkpoint_kind="recovery_card"
                )
            )
            == 1
        )
        activity = self.call_api("/api/system/activity-log?limit=200")
        assert activity.status_code == 200
        events = [
            entry.get("data", {})
            for entry in activity.json()["entries"]
            if entry.get("data", {}).get("session_id") == session_id
        ]
        assert any(
            event.get("event") == "chat_compaction_strategy_changed"
            and event.get("reason") == "checkpoint_strategy_changed"
            for event in events
        )
        assert not any(
            event.get("event") == "chat_compaction_failed" for event in events
        )

    async def _test_failure_logging(self, vault_name: str) -> None:
        """Distinct failed upgrades survive dedupe without exposing model contents."""
        runtime = get_runtime_context()
        failed_ids: set[str] = set()
        sentinel = "PRIVATE-UPGRADE-PROMPT-SENTINEL " + (
            "private transcript contents " * 500
        )

        async def failing_author(**_kwargs: object) -> SessionMapDraft:
            raise RuntimeError(sentinel)

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=failing_author,
        ):
            for suffix in ("one", "two"):
                session_id = f"strategy-upgrade-log-failure-{suffix}"
                _seed_v1(runtime.chat_store, session_id, vault_name)
                started = await _start_upgrade(session_id, vault_name)
                failed_ids.add(started.task_id)
                terminal = await _wait_for_terminal_task(started.task_id)
                assert terminal.status == "failed"
                assert terminal.terminal_error_type == "RuntimeError"
                assert "PRIVATE-UPGRADE-PROMPT-SENTINEL" not in str(
                    terminal.terminal_reason
                )
                task_response = self.call_api(f"/api/tasks/{started.task_id}")
                assert task_response.status_code == 200
                assert "PRIVATE-UPGRADE-PROMPT-SENTINEL" not in task_response.text

        response = self.call_api("/api/system/activity-log?limit=200")
        assert response.status_code == 200
        children = [
            task
            for task in await runtime.task_coordinator.list_tasks()
            if task.parent_task_id in failed_ids
        ]
        assert all(
            "PRIVATE-UPGRADE-PROMPT-SENTINEL" not in str(task.terminal_reason)
            for task in children
        )
        correlated_ids = failed_ids | {task.task_id for task in children}
        correlated = [
            entry
            for entry in response.json()["entries"]
            if entry.get("data", {}).get("task_id") in correlated_ids
        ]
        assert "PRIVATE-UPGRADE-PROMPT-SENTINEL" not in json.dumps(correlated)
        assert "private transcript contents" not in json.dumps(correlated)
        rows = [
            entry["data"]
            for entry in response.json()["entries"]
            if entry.get("tag") == "context-strategy-upgrade"
            and entry.get("data", {}).get("event") == "context_strategy_upgrade_failed"
            and entry["data"].get("task_id") in failed_ids
        ]
        assert len(rows) == 2, "Warning dedupe must not hide a later failed upgrade"
        assert len({row["issue"] for row in rows}) == 2
        assert all(
            row["status"] == "failed" and row["reason"] == "upgrade_execution_failed"
            for row in rows
        )
        assert all(
            row["source"] == "api" and row["error_type"] == "RuntimeError"
            for row in rows
        )
        assert all(len(row["error"]) < 100 for row in rows)
        assert "PRIVATE-UPGRADE-PROMPT-SENTINEL" not in json.dumps(rows)
        assert "private transcript contents" not in json.dumps(rows)
        # Failed reconstruction passes remain Activity-visible despite quiet success detail.
        author_failures = [
            entry["data"]
            for entry in response.json()["entries"]
            if entry.get("tag") == "session-map-authoring"
            and entry.get("data", {}).get("event") == "session_map_authoring_failed"
            and entry["data"].get("parent_task_id") in failed_ids
        ]
        assert len(author_failures) == 2
        assert "PRIVATE-UPGRADE-PROMPT-SENTINEL" not in json.dumps(author_failures)

    async def _test_cancellation_and_restart(self, vault_name: str) -> None:
        runtime = get_runtime_context()
        store = runtime.chat_store
        session_id = "strategy-upgrade-cancelled"
        _seed_v1(store, session_id, vault_name)
        raw_before = store.get_history(session_id, vault_name, mode="raw")
        effective_before = store.get_history(session_id, vault_name)
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked_author(**kwargs: object) -> SessionMapDraft:
            started.set()
            await release.wait()
            return _cumulative_draft(kwargs)

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=blocked_author,
        ):
            starts = await asyncio.gather(
                *(_start_upgrade(session_id, vault_name) for _ in range(3))
            )
            assert len({task.task_id for task in starts}) == 1
            task_id = starts[0].task_id
            await asyncio.wait_for(started.wait(), timeout=2)
            await runtime.task_coordinator.cancel_task(task_id)
            cancelled = await _wait_for_terminal_task(task_id)
            assert cancelled is not None and cancelled.status == "cancelled"
            assert cancelled.result is None
            children = await runtime.task_coordinator.list_child_tasks(task_id)
            assert children and all(child.status == "cancelled" for child in children)
            assert store.get_history(session_id, vault_name, mode="raw") == raw_before
            assert store.get_history(session_id, vault_name) == effective_before
            assert (
                store.list_context_checkpoints(
                    session_id, vault_name, checkpoint_kind="session_map"
                )
                == []
            )

            # Retry without a restart must release both operation and history locks.
            started.clear()
            pending = await _start_upgrade(session_id, vault_name)
            assert pending.task_id != task_id
            await asyncio.wait_for(started.wait(), timeout=2)
            await self.restart_system()
            shutdown_task = await runtime.task_coordinator.get_task(pending.task_id)
            assert shutdown_task is not None and shutdown_task.status == "cancelled"
            restarted = get_runtime_context()
            assert (
                restarted.chat_store.get_history(session_id, vault_name, mode="raw")
                == raw_before
            )
            assert (
                restarted.chat_store.get_history(session_id, vault_name)
                == effective_before
            )
            release.set()
            retry = await _start_upgrade(session_id, vault_name)
            completed = await _wait_for_terminal_task(retry.task_id)
            assert completed is not None and completed.status == "completed"
            assert completed.result and completed.result["checkpoint_id"]
            assert (
                restarted.chat_store.get_history(session_id, vault_name, mode="raw")
                == raw_before
            )
            assert (
                len(
                    restarted.chat_store.list_context_checkpoints(
                        session_id, vault_name, checkpoint_kind="session_map"
                    )
                )
                == 1
            )


def _seed_v1(store: ChatStore, session_id: str, vault_name: str) -> None:
    store.ensure_session(
        session_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
    )
    store.add_messages(
        session_id, vault_name, _long_conversation(session_id, pair_count=6)
    )
    _add_recovery_checkpoint(
        store=store,
        session_id=session_id,
        vault_name=vault_name,
        checkpoint_id=f"{session_id}-recovery",
        boundary=5,
    )


async def _start_upgrade(session_id: str, vault_name: str) -> ExecutionTaskSnapshot:
    return await start_session_context_strategy_upgrade(
        session_id=session_id, vault_name=vault_name, authority=LOCAL_USER_AUTHORITY
    )


def _cumulative_draft(kwargs: dict[str, object]) -> SessionMapDraft:
    prompt = json.loads(str(kwargs["prompt"]))
    ranges = prompt["new_evidence_envelopes"]
    previous = prompt["previous_map"].get("trajectory")
    sources = (
        [SourceRange.model_validate(source) for source in previous["sources"]]
        if previous
        else []
    )
    sources.extend(
        SourceRange.model_validate(source)
        for envelope in ranges
        for source in envelope["citable_source_ranges"]
    )
    return SessionMapDraft(
        trajectory=SessionMapTrajectory(
            text="Cumulative reconstruction of canonical history.",
            sources=tuple(sources),
        )
    )


async def _wait_for_queued_task(task_id: str) -> ExecutionTaskSnapshot:
    runtime = get_runtime_context()
    for _ in range(100):
        task = await runtime.task_coordinator.get_task(task_id)
        if task is not None and task.metadata.get("queue_position", 0) > 0:
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("Upgrade did not enter the session task queue")


def _add_recovery_checkpoint(
    *,
    store: ChatStore,
    session_id: str,
    vault_name: str,
    checkpoint_id: str,
    boundary: int,
) -> None:
    summary = build_compaction_summary_message(
        f"Existing recovery state through message {boundary}."
    )
    store.add_compaction_checkpoint(
        session_id=session_id,
        vault_name=vault_name,
        checkpoint_id=checkpoint_id,
        source="validation",
        message_count_before=boundary + 1,
        last_message_sequence_index=boundary,
        summary_message=summary,
        replacement_history=[summary],
    )


def _long_conversation(prefix: str, *, pair_count: int) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for index in range(pair_count):
        detail = f"{prefix}-{index}-" + ("substantive planning evidence " * 12)
        messages.extend(
            [
                _user(f"User contribution {detail}"),
                _assistant(f"Assistant response {detail}"),
            ]
        )
    return messages


async def _wait_for_terminal_task(task_id: str) -> ExecutionTaskSnapshot:
    runtime = get_runtime_context()
    for _ in range(250):
        task = await runtime.task_coordinator.get_task(task_id)
        if task is not None and task.is_terminal:
            return task
        await asyncio.sleep(0.02)
    raise AssertionError(f"Upgrade task did not finish: {task_id}")


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
