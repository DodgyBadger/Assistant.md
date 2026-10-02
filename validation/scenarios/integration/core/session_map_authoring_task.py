"""Validate governed session-map authoring and provenance failure handling."""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import LOCAL_USER_AUTHORITY  # noqa: E402
from core.memory.session_map.evidence import (  # noqa: E402
    SessionMapEvidence,
    SessionMapMessageEvidence,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SessionMapProvenanceError,
    SessionMapTrajectory,
    SourceRange,
    validate_session_map_provenance,
)
from core.memory.session_map.service import (  # noqa: E402
    SessionMapAuthoringRequest,
    _invoke_session_map_model,
    run_session_map_authoring,
)
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    ExecutionTaskSource,
    get_current_execution_task,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SessionMapAuthoringTaskScenario(BaseScenario):
    """Keep model-backed map updates inside observable execution tasks."""

    async def test_scenario(self) -> None:
        await self.start_system()
        runtime = get_runtime_context()
        envelopes = (_envelope(),)
        request = SessionMapAuthoringRequest(
            session_id="session-map-task",
            vault_name="SessionMapTaskVault",
            model_alias="gpt-mini",
            previous_map=SessionMapDraft(),
            new_evidence=envelopes,
            recent_evidence=(
                SessionMapMessageEvidence(
                    sequence_index=12,
                    role="user",
                    content_text="The legal review is complete.",
                ),
            ),
        )
        authored = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The legal-review work concluded after the user confirmed completion.",
                sources=(SourceRange(start=10, end=12),),
            ),
            entries=(
                SessionMapEntry(
                    id="legal_review_completed",
                    kind="decision",
                    state="closed",
                    basis="user_established",
                    text="The legal review is complete.",
                    sources=(SourceRange(start=12, end=12),),
                ),
            ),
        )
        observed_tasks: list[tuple[str, str]] = []

        async def return_grounded_map(**_kwargs: object) -> SessionMapDraft:
            task = get_current_execution_task()
            assert task is not None
            observed_tasks.append((task.task_id, task.kind))
            return authored

        checkpoint = self.event_checkpoint()
        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=return_grounded_map,
        ):
            result = await run_session_map_authoring(
                request,
                authority=LOCAL_USER_AUTHORITY,
                source=ExecutionTaskSource.SYSTEM,
            )

        self.soft_assert_equal(
            result.draft,
            authored,
            "The governed author should return the validated replacement map",
        )
        self.soft_assert_equal(
            (
                result.model_alias,
                result.thinking,
                result.prompt_contract_version,
                result.new_evidence_count,
                result.source_history_revision,
                result.evidence_source_start,
                result.evidence_source_end,
            ),
            ("gpt-mini", None, "eviction-map-v9", 1, 2, 10, 11),
            "The author should return enough provenance for any persistence adapter",
        )
        self.soft_assert_equal(
            observed_tasks,
            [(result.task_id, ExecutionTaskKind.SESSION_MAP_AUTHORING.value)],
            "The model boundary should execute inside its owning map task",
        )
        completed_task = await runtime.task_coordinator.get_task(result.task_id)
        self.soft_assert_equal(
            completed_task.status if completed_task else None,
            "completed",
            "Valid map authoring should complete its execution task",
        )
        self.soft_assert_equal(
            completed_task.scope if completed_task else None,
            "chat_session:session-map-task",
            "Map authoring should share the chat session task scope",
        )
        authoring_events = [
            event.get("name")
            for event in self.events_since(checkpoint)
            if event.get("data", {}).get("task_id") == result.task_id
        ]
        self.soft_assert(
            "session_map_authoring_started" in authoring_events,
            "Map authoring should log its model boundary start",
        )
        self.soft_assert(
            "session_map_authoring_completed" in authoring_events,
            "Map authoring should log its validated completion",
        )
        activity = self.call_api("/api/system/activity-log?limit=200")
        assert activity.status_code == 200
        ordinary_events = [
            entry["data"]
            for entry in activity.json()["entries"]
            if entry.get("tag") == "session-map-authoring"
            and entry.get("data", {}).get("task_id") == result.task_id
        ]
        assert {event["status"] for event in ordinary_events} == {
            "running",
            "completed",
        }
        assert all(event["source"] == "system" for event in ordinary_events)
        assert all(event["parent_task_id"] is None for event in ordinary_events)

        unsupported = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The legal-review trajectory remains grounded.",
                sources=(SourceRange(start=10, end=10),),
            ),
            entries=(
                SessionMapEntry(
                    id="unsupported_decision",
                    kind="decision",
                    state="active",
                    basis="user_established",
                    text="An unavailable decision.",
                    sources=(SourceRange(start=99, end=99),),
                ),
            ),
        )

        async def return_unsupported_map(**_kwargs: object) -> SessionMapDraft:
            return unsupported

        failure_checkpoint = self.event_checkpoint()
        try:
            with patch(
                "core.memory.session_map.service._invoke_session_map_model",
                new=return_unsupported_map,
            ):
                await run_session_map_authoring(
                    request,
                    authority=LOCAL_USER_AUTHORITY,
                    source=ExecutionTaskSource.SYSTEM,
                )
        except SessionMapProvenanceError:
            pass
        else:
            raise AssertionError("Unsupported model provenance should fail authoring")

        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        failed_task = tasks[-1]
        self.soft_assert_equal(
            failed_task.status,
            "failed",
            "Unavailable provenance should fail the owning execution task",
        )
        self.soft_assert_equal(
            failed_task.terminal_error_type,
            "SessionMapProvenanceError",
            "The task should retain the provenance failure category",
        )
        failure_events = [
            event.get("name")
            for event in self.events_since(failure_checkpoint)
            if event.get("data", {}).get("task_id") == failed_task.task_id
        ]
        self.soft_assert(
            "session_map_authoring_failed" in failure_events,
            "Rejected provenance should emit a domain failure event",
        )
        await self._test_content_safe_distinct_failures(request)

        structured_calls = 0

        async def structured_stream(
            _messages: object,
            info: AgentInfo,
        ) -> AsyncIterator[dict[int, DeltaToolCall]]:
            nonlocal structured_calls
            structured_calls += 1
            output_tool = info.model_request_parameters.output_tools[0]
            arguments = (
                '{"entries":[{}]}' if structured_calls == 1 else '{"entries":[]}'
            )
            yield {
                0: DeltaToolCall(
                    name=output_tool.name,
                    json_args=arguments,
                    tool_call_id=f"session-map-output-{structured_calls}",
                )
            }

        with patch(
            "core.memory.session_map.service.build_model_instance",
            return_value=FunctionModel(stream_function=structured_stream),
        ):
            retried = await _invoke_session_map_model(
                model_alias="gpt-mini",
                thinking="low",
                prompt="Return a session map.",
            )
        self.soft_assert_equal(
            retried,
            SessionMapDraft(),
            "Map authoring should return corrected structured output",
        )
        self.soft_assert_equal(
            structured_calls,
            2,
            "Map authoring should retain structured-output retries over streaming transport",
        )

        provenance_calls = 0
        unavailable = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The draft cites a message outside the supplied evidence.",
                sources=(SourceRange(start=99, end=99),),
            )
        )
        grounded = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The draft is grounded in the supplied evidence.",
                sources=(SourceRange(start=10, end=11),),
            )
        )

        async def provenance_stream(
            _messages: object,
            info: AgentInfo,
        ) -> AsyncIterator[dict[int, DeltaToolCall]]:
            nonlocal provenance_calls
            provenance_calls += 1
            output_tool = info.model_request_parameters.output_tools[0]
            draft = unavailable if provenance_calls == 1 else grounded
            yield {
                0: DeltaToolCall(
                    name=output_tool.name,
                    json_args=draft.model_dump_json(),
                    tool_call_id=f"session-map-provenance-{provenance_calls}",
                )
            }

        def require_available_provenance(draft: SessionMapDraft) -> SessionMapDraft:
            return validate_session_map_provenance(draft, evidence=envelopes)

        with patch(
            "core.memory.session_map.service.build_model_instance",
            return_value=FunctionModel(stream_function=provenance_stream),
        ):
            provenance_retried = await _invoke_session_map_model(
                model_alias="gpt-mini",
                thinking="low",
                prompt="Return a grounded session map.",
                output_validator=require_available_provenance,
            )
        self.soft_assert_equal(
            provenance_retried,
            grounded,
            "Map authoring should return the provenance-corrected output",
        )
        self.soft_assert_equal(
            provenance_calls,
            2,
            "Unavailable provenance should receive a normal structured-output retry",
        )

        self.assert_no_failures()

    async def _test_content_safe_distinct_failures(
        self, request: SessionMapAuthoringRequest
    ) -> None:
        sentinel = "PRIVATE-AUTHOR-PROMPT-SENTINEL " + (
            "secret document contents " * 500
        )
        failed_ids: set[str] = set()

        async def failing_model(**_kwargs: object) -> SessionMapDraft:
            task = get_current_execution_task()
            assert task is not None
            failed_ids.add(task.task_id)
            raise RuntimeError(sentinel)

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=failing_model,
        ):
            for suffix in ("one", "two"):
                session_id = f"session-map-failure-{suffix}"
                distinct = replace(
                    request,
                    session_id=session_id,
                    new_evidence=(
                        replace(request.new_evidence[0], session_id=session_id),
                    ),
                )
                try:
                    await run_session_map_authoring(
                        distinct, authority=LOCAL_USER_AUTHORITY
                    )
                except RuntimeError:
                    pass
                else:
                    raise AssertionError("The owning author task must fail")

        response = self.call_api("/api/system/activity-log?limit=200")
        assert response.status_code == 200
        rows = [
            entry["data"]
            for entry in response.json()["entries"]
            if entry.get("tag") == "session-map-authoring"
            and entry.get("data", {}).get("event") == "session_map_authoring_failed"
            and entry["data"].get("task_id") in failed_ids
        ]
        assert len(rows) == 2, "Warning dedupe must not erase distinct author failures"
        assert len({row["issue"] for row in rows}) == 2
        assert all(
            row["status"] == "failed" and row["reason"] == "authoring_failed"
            for row in rows
        )
        assert all(
            row["error_type"] == "RuntimeError" and len(row["error"]) < 100
            for row in rows
        )
        assert "PRIVATE-AUTHOR-PROMPT-SENTINEL" not in json.dumps(rows)
        assert "secret document contents" not in json.dumps(rows)


def _envelope() -> SessionMapEvidence:
    return SessionMapEvidence(
        evidence_id="session-map-task-envelope",
        session_id="session-map-task",
        vault_name="SessionMapTaskVault",
        history_revision=2,
        source_start_sequence_index=10,
        source_end_sequence_index=11,
        message_count=2,
        estimated_tokens=100,
        projected_text="[source:10] USER:\nPlease review this with counsel.",
        source_digest="session-map-task-digest",
    )
