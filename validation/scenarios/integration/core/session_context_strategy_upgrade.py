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
    UserPromptPart,
)

from core.chat.chat_store import ChatStore  # noqa: E402
from core.chat.compaction import build_compaction_summary_message  # noqa: E402
from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
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
    get_current_execution_task,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


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
            ("compaction_token_threshold", "500"),
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

        sessions_response = self.call_api(f"/api/chat/sessions?vault_name={vault.name}")
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
        self.assert_no_failures()


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


async def _wait_for_terminal_task(task_id: str):
    runtime = get_runtime_context()
    for _ in range(250):
        task = await runtime.task_coordinator.get_task(task_id)
        if task is not None and task.is_terminal:
            return task
        await asyncio.sleep(0.02)
    return None


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
