"""Validate governed session-map authoring and provenance failure handling."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import patch

from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.compaction import CanonicalEvictionEnvelope  # noqa: E402
from core.identity import LOCAL_USER_AUTHORITY  # noqa: E402
from core.memory.session_map.authoring import SessionMapRetainedEvidence  # noqa: E402
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SessionMapProvenanceError,
    SessionMapTrajectory,
    SourceRange,
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
            envelopes=envelopes,
            retained_evidence=(
                SessionMapRetainedEvidence(
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

        self.assert_no_failures()


def _envelope() -> CanonicalEvictionEnvelope:
    return CanonicalEvictionEnvelope(
        envelope_id="session-map-task-envelope",
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
