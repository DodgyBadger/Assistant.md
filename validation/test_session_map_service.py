"""Deterministic coverage for opt-in shadow session-map maintenance."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = tempfile.TemporaryDirectory(prefix="assistantmd-session-service-import-")
_TEST_ROOT_PATH = Path(_TEST_ROOT.name)
set_bootstrap_roots(_TEST_ROOT_PATH / "data", _TEST_ROOT_PATH / "system")

import core.chat.chat_store as chat_store_module  # noqa: E402
from core.chat.chat_store import ChatStore  # noqa: E402
from core.identity import (  # noqa: E402
    LOCAL_USER_AUTHORITY,
    LOCAL_USER_PRINCIPAL_ID,
)
from core.llm.decision import (  # noqa: E402
    DecisionClassifier,
    DecisionMetadata,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
)
from core.memory.session_map.authoring import (  # noqa: E402
    SessionMapAuthoringRequest,
    SessionMapAuthoringResult,
)
from core.memory.session_map.change_detection import (  # noqa: E402
    SessionMapCumulativeChangeSignals,
)
from core.memory.session_map.models import (  # noqa: E402
    AddPatch,
    ChangeAttentionPatch,
    GoalEntry,
    GoalStatus,
    MapPatchSet,
    SourceRef,
    apply_patch_set,
)
from core.memory.session_map.service import (  # noqa: E402
    SessionMapService,
    SessionMemoryPolicy,
)
from core.memory.session_map.store import SessionMapStore  # noqa: E402
from core.runtime.background import RuntimeBackgroundSpawner  # noqa: E402
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    ExecutionTaskSnapshot,
    ExecutionTaskSource,
    TaskCoordinator,
)
from core.runtime.task_runner import ExecutionTaskRunner  # noqa: E402

NOW = datetime(2026, 9, 26, tzinfo=UTC)


class _SequencedClassifier:
    def __init__(self, broad_scores: list[float]) -> None:
        self._broad_scores = iter(broad_scores)
        self.requests: list[str] = []

    async def classify(
        self,
        request: DecisionRequest[SessionMapCumulativeChangeSignals],
    ) -> DecisionResult[SessionMapCumulativeChangeSignals]:
        self.requests.append(request.state)
        broad = next(self._broad_scores)
        values = {
            field: (broad if field == "reconciliation_needed" else 0.1)
            for field in SessionMapCumulativeChangeSignals.model_fields
        }
        return DecisionResult(
            output=SessionMapCumulativeChangeSignals.model_validate(values),
            requested_model_alias="jev",
            resolved_model_name="jev-test",
            provider_name="typesafe",
            latency_seconds=0.01,
            usage=DecisionUsage(requests=1, input_tokens=100, output_tokens=10),
            metadata=DecisionMetadata(),
        )


class _BlockingClassifier(_SequencedClassifier):
    def __init__(
        self,
        broad_scores: list[float],
        *,
        first_started: asyncio.Event,
        release_first: asyncio.Event,
        second_started: asyncio.Event,
    ) -> None:
        super().__init__(broad_scores)
        self._first_started = first_started
        self._release_first = release_first
        self._second_started = second_started

    async def classify(
        self,
        request: DecisionRequest[SessionMapCumulativeChangeSignals],
    ) -> DecisionResult[SessionMapCumulativeChangeSignals]:
        request_number = len(self.requests) + 1
        if request_number == 1:
            self._first_started.set()
            await self._release_first.wait()
        elif request_number == 2:
            self._second_started.set()
        return await super().classify(request)


@pytest.mark.asyncio
async def test_cumulative_skip_waits_for_new_interval_then_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_store_module, "get_persist_model_reasoning_parts", lambda: False
    )
    with tempfile.TemporaryDirectory(prefix="assistantmd-session-service-") as root:
        chat_store = ChatStore(root)
        map_store = SessionMapStore(root)
        session_id = "session-service"
        vault_name = "Vault"
        chat_store.ensure_session(
            session_id,
            vault_name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        coordinator = TaskCoordinator()
        background_tasks: set[asyncio.Task] = set()
        runner = ExecutionTaskRunner(
            task_coordinator=coordinator,
            background_spawner=RuntimeBackgroundSpawner(
                background_loop=asyncio.get_running_loop(),
                background_tasks=background_tasks,
            ),
        )
        classifier = _SequencedClassifier([0.1, 0.9])
        policy = SessionMemoryPolicy(
            mode="observe",
            decision_model="jev",
            author_model="gpt-mini",
            eligibility_turns=2,
            broad_change_threshold=0.25,
            field_change_threshold=0.5,
            max_pending_turns=30,
            max_pending_tokens=80_000,
            task_timeout_seconds=10,
            max_concurrent_tasks=2,
        )
        authored_deltas: list[tuple[int, ...]] = []

        async def _author(
            *,
            model_alias: str,
            request: SessionMapAuthoringRequest,
            created_at: datetime,
        ) -> SessionMapAuthoringResult:
            authored_deltas.append(
                tuple(message.sequence_index for message in request.delta)
            )
            patch = MapPatchSet(
                expected_revision=request.current_map.revision,
                through_sequence_index=request.through_sequence_index,
                observed_source_content_revision=(
                    request.observed_source_content_revision
                ),
                operations=(
                    AddPatch(
                        entry=GoalEntry(
                            id="goal_observe",
                            text="Exercise shadow session memory.",
                            status=GoalStatus.ACTIVE,
                            source_refs=(SourceRef(sequence_index=0, role="user"),),
                        )
                    ),
                    ChangeAttentionPatch(
                        active_goal_ids=("goal_observe",),
                        evidence_refs=(SourceRef(sequence_index=0, role="user"),),
                    ),
                ),
            )
            result_map = apply_patch_set(
                request.current_map,
                patch,
                created_at=created_at,
            )
            return SessionMapAuthoringResult(
                patch_set=patch,
                session_map=result_map,
                requested_model_alias=model_alias,
                requested_thinking="default",
                resolved_model_name="author-test",
                provider_name="test",
                latency_seconds=0.02,
                requests=1,
                input_tokens=200,
                output_tokens=20,
            )

        service = SessionMapService(
            chat_store=chat_store,
            map_store=map_store,
            task_runner=runner,
            policy_loader=lambda: policy,
            classifier_builder=lambda _alias: cast(DecisionClassifier, classifier),
            author=_author,
            now=lambda: NOW,
        )

        _record_turn(service, chat_store, session_id, vault_name, 1)
        _record_turn(service, chat_store, session_id, vault_name, 2)
        first_task_id = await _schedule_from_parent(
            service,
            coordinator,
            session_id=session_id,
            vault_name=vault_name,
        )
        await _wait_terminal(coordinator, first_task_id)

        skipped = map_store.get_maintenance_state(session_id, vault_name)
        assert skipped is not None
        assert skipped.pending_turn_count == 2
        assert skipped.decision_checked_pending_turn_count == 2
        assert skipped.decision_checked_through_sequence_index == 3
        assert map_store.get_latest_revision(session_id, vault_name) is None

        _record_turn(service, chat_store, session_id, vault_name, 3)
        assert (
            await service.maybe_schedule_after_turn(
                session_id=session_id,
                vault_name=vault_name,
                authority=LOCAL_USER_AUTHORITY,
                parent_task_id="unused-while-ineligible",
            )
            is None
        )

        _record_turn(service, chat_store, session_id, vault_name, 4)
        second_task_id = await _schedule_from_parent(
            service,
            coordinator,
            session_id=session_id,
            vault_name=vault_name,
        )
        second = await _wait_terminal(coordinator, second_task_id)
        assert second.status == "completed"
        assert second.kind == ExecutionTaskKind.SESSION_MEMORY
        assert second.detached_from_parent_lifecycle is True

        committed = map_store.get_latest_revision(session_id, vault_name)
        assert committed is not None
        assert committed.updated_through_sequence_index == 7
        assert committed.decision_model == "jev-test"
        assert committed.authoring_model == "author-test"
        assert authored_deltas == [tuple(range(8))]
        assert len(classifier.requests) == 2
        assert "turn 1" in classifier.requests[0]
        assert "turn 4" in classifier.requests[1]
        final_state = map_store.get_maintenance_state(session_id, vault_name)
        assert final_state is not None
        assert final_state.status == "idle"
        assert final_state.pending_turn_count == 0

        with sqlite3.connect(f"{root}/chat_sessions.db") as conn:
            attempts = conn.execute(
                """
                SELECT status FROM chat_session_map_attempts
                WHERE session_id = ? ORDER BY attempt_number
                """,
                (session_id,),
            ).fetchall()
        assert attempts == [("skipped",), ("committed",)]
        await coordinator.shutdown(reason="test_complete")
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_turns_arriving_during_attempt_trigger_coalesced_catch_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_store_module, "get_persist_model_reasoning_parts", lambda: False
    )
    with tempfile.TemporaryDirectory(prefix="assistantmd-session-catch-up-") as root:
        chat_store = ChatStore(root)
        map_store = SessionMapStore(root)
        session_id = "session-catch-up"
        vault_name = "Vault"
        chat_store.ensure_session(
            session_id,
            vault_name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        coordinator = TaskCoordinator()
        background_tasks: set[asyncio.Task] = set()
        runner = ExecutionTaskRunner(
            task_coordinator=coordinator,
            background_spawner=RuntimeBackgroundSpawner(
                background_loop=asyncio.get_running_loop(),
                background_tasks=background_tasks,
            ),
        )
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        second_started = asyncio.Event()
        classifier = _BlockingClassifier(
            [0.1, 0.1],
            first_started=first_started,
            release_first=release_first,
            second_started=second_started,
        )
        policy = SessionMemoryPolicy(
            mode="observe",
            decision_model="jev",
            author_model="gpt-mini",
            eligibility_turns=2,
            broad_change_threshold=0.25,
            field_change_threshold=0.5,
            max_pending_turns=30,
            max_pending_tokens=80_000,
            task_timeout_seconds=10,
            max_concurrent_tasks=2,
        )
        service = SessionMapService(
            chat_store=chat_store,
            map_store=map_store,
            task_runner=runner,
            policy_loader=lambda: policy,
            classifier_builder=lambda _alias: cast(DecisionClassifier, classifier),
            now=lambda: NOW,
        )

        _record_turn(service, chat_store, session_id, vault_name, 1)
        _record_turn(service, chat_store, session_id, vault_name, 2)
        first_task_id = await _schedule_from_parent(
            service,
            coordinator,
            session_id=session_id,
            vault_name=vault_name,
        )
        await asyncio.wait_for(first_started.wait(), timeout=3)

        _record_turn(service, chat_store, session_id, vault_name, 3)
        _record_turn(service, chat_store, session_id, vault_name, 4)
        assert (
            await service.maybe_schedule_after_turn(
                session_id=session_id,
                vault_name=vault_name,
                authority=LOCAL_USER_AUTHORITY,
                parent_task_id="coalesced-while-running",
            )
            is None
        )

        release_first.set()
        await asyncio.wait_for(second_started.wait(), timeout=3)
        tasks = await coordinator.list_tasks(kind=ExecutionTaskKind.SESSION_MEMORY)
        assert len(tasks) == 2
        assert tasks[1].parent_task_id == first_task_id
        result = await coordinator.wait_for_tasks(
            [tasks[1].task_id],
            timeout_seconds=3,
            terminal_or_attention_only=True,
        )
        assert result.timed_out is False
        assert result.snapshots[0].status == "skipped"
        completed_tasks = await coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MEMORY
        )
        assert [task.status for task in completed_tasks] == ["skipped", "skipped"]
        assert len(classifier.requests) == 2
        assert "turn 2" in classifier.requests[0]
        assert "turn 3" not in classifier.requests[0]
        assert "turn 1" in classifier.requests[1]
        assert "turn 4" in classifier.requests[1]

        state = map_store.get_maintenance_state(session_id, vault_name)
        assert state is not None
        assert state.pending_turn_count == 4
        assert state.decision_checked_pending_turn_count == 4
        assert state.decision_checked_through_sequence_index == 7
        with sqlite3.connect(f"{root}/chat_sessions.db") as conn:
            attempts = conn.execute(
                """
                SELECT status, through_sequence_index
                FROM chat_session_map_attempts
                WHERE session_id = ? ORDER BY attempt_number
                """,
                (session_id,),
            ).fetchall()
        assert attempts == [("skipped", 3), ("skipped", 7)]
        await coordinator.shutdown(reason="test_complete")
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)


def test_off_mode_does_not_create_maintenance_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        chat_store_module, "get_persist_model_reasoning_parts", lambda: False
    )
    with tempfile.TemporaryDirectory(prefix="assistantmd-session-service-off-") as root:
        chat_store = ChatStore(root)
        map_store = SessionMapStore(root)
        chat_store.ensure_session(
            "off-session",
            "Vault",
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        policy = SessionMemoryPolicy(
            mode="off",
            decision_model="jev",
            author_model="gpt-mini",
            eligibility_turns=3,
            broad_change_threshold=0.25,
            field_change_threshold=0.5,
            max_pending_turns=30,
            max_pending_tokens=80_000,
            task_timeout_seconds=180,
            max_concurrent_tasks=2,
        )
        service = SessionMapService(
            chat_store=chat_store,
            map_store=map_store,
            task_runner=ExecutionTaskRunner(
                task_coordinator=TaskCoordinator(),
                background_spawner=RuntimeBackgroundSpawner(),
            ),
            policy_loader=lambda: policy,
        )
        with chat_store.transaction() as connection:
            chat_store.add_messages(
                "off-session",
                "Vault",
                [ModelRequest(parts=[UserPromptPart(content="hello")])],
                connection=connection,
            )
            assert (
                service.record_completed_turn(
                    connection,
                    session_id="off-session",
                    vault_name="Vault",
                )
                is None
            )
        assert map_store.get_maintenance_state("off-session", "Vault") is None


def _record_turn(
    service: SessionMapService,
    chat_store: ChatStore,
    session_id: str,
    vault_name: str,
    number: int,
) -> None:
    with chat_store.transaction() as connection:
        chat_store.add_messages(
            session_id,
            vault_name,
            [
                ModelRequest(parts=[UserPromptPart(content=f"turn {number} user")]),
                ModelResponse(parts=[TextPart(content=f"turn {number} assistant")]),
            ],
            connection=connection,
        )
        service.record_completed_turn(
            connection,
            session_id=session_id,
            vault_name=vault_name,
        )


async def _schedule_from_parent(
    service: SessionMapService,
    coordinator: TaskCoordinator,
    *,
    session_id: str,
    vault_name: str,
) -> str:
    async with coordinator.track_current_task(
        kind=ExecutionTaskKind.CHAT,
        scope=f"chat_session:{session_id}",
        source=ExecutionTaskSource.API,
        label=f"chat:{session_id}",
        authority=LOCAL_USER_AUTHORITY,
    ) as parent:
        task = await service.maybe_schedule_after_turn(
            session_id=session_id,
            vault_name=vault_name,
            authority=LOCAL_USER_AUTHORITY,
            parent_task_id=parent.task_id,
        )
        assert task is not None
        return task.task_id


async def _wait_terminal(
    coordinator: TaskCoordinator, task_id: str
) -> ExecutionTaskSnapshot:
    result = await coordinator.wait_for_tasks(
        [task_id],
        timeout_seconds=3,
        terminal_or_attention_only=True,
    )
    assert result.timed_out is False
    assert len(result.snapshots) == 1
    assert result.snapshots[0].is_terminal
    return result.snapshots[0]
