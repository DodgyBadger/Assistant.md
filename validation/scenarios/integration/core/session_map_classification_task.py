"""Validate governed scalar classification for cumulative session-map evidence."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.chat.compaction import CanonicalEvictionEnvelope  # noqa: E402
from core.identity import LOCAL_USER_AUTHORITY  # noqa: E402
from core.llm.decision import (  # noqa: E402
    DecisionMetadata,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    DecisionUsage,
)
from core.memory.session_map.gate import (  # noqa: E402
    SessionMapGateRequest,
    SessionMapMovementDecision,
    run_session_map_gate,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SourceRange,
)
from core.runtime.execution_tasks import (  # noqa: E402
    ExecutionTaskKind,
    get_current_execution_task,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SessionMapClassificationTaskScenario(BaseScenario):
    """Keep the optional movement gate typed, scalar, and task governed."""

    async def test_scenario(self) -> None:
        await self.start_system()
        runtime = get_runtime_context()
        request = SessionMapGateRequest(
            session_id="session-map-classification",
            vault_name="SessionMapClassificationVault",
            model_alias="jev",
            current_map=_current_map(),
            envelopes=(_envelope(),),
        )
        observed_requests: list[DecisionRequest[SessionMapMovementDecision]] = []
        observed_tasks: list[tuple[str, str]] = []

        class SuccessfulClassifier:
            async def classify(
                self,
                decision_request: DecisionRequest[SessionMapMovementDecision],
            ) -> DecisionResult[SessionMapMovementDecision]:
                task = get_current_execution_task()
                assert task is not None
                observed_tasks.append((task.task_id, task.kind))
                observed_requests.append(decision_request)
                return DecisionResult(
                    output=SessionMapMovementDecision(
                        material_map_update_probability=0.72
                    ),
                    requested_model_alias="jev",
                    resolved_model_name="jev-latest",
                    provider_name="typesafe",
                    latency_seconds=0.12,
                    usage=DecisionUsage(
                        requests=1,
                        input_tokens=321,
                        output_tokens=4,
                    ),
                    metadata=DecisionMetadata(),
                )

        checkpoint = self.event_checkpoint()
        with patch(
            "core.memory.session_map.gate.build_decision_classifier",
            return_value=SuccessfulClassifier(),
        ):
            result = await run_session_map_gate(
                request,
                authority=LOCAL_USER_AUTHORITY,
            )

        self.soft_assert_equal(
            result.score,
            0.72,
            "The governed gate should expose one bounded movement probability",
        )
        self.soft_assert_equal(
            observed_tasks,
            [(result.task_id, ExecutionTaskKind.SESSION_MAP_CLASSIFICATION.value)],
            "Decision dispatch should execute inside its owning classification task",
        )
        self.soft_assert_equal(
            len(observed_requests),
            1,
            "The gate should make one classifier request",
        )
        self.soft_assert(
            '"current_map"' in observed_requests[0].state
            and '"cumulative_new_evidence_envelopes"' in observed_requests[0].state
            and "material_map_update_probability"
            in str(observed_requests[0].output_type.model_json_schema()),
            "The classifier should receive the current map, cumulative evidence, and scalar schema",
        )
        self.soft_assert(
            result.input_token_estimate > 0,
            "The gate should estimate its complete input before dispatch",
        )
        completed = await runtime.task_coordinator.get_task(result.task_id)
        self.soft_assert_equal(
            completed.status if completed else None,
            "completed",
            "A valid classification should complete its execution task",
        )
        self.soft_assert_equal(
            completed.scope if completed else None,
            "chat_session:session-map-classification",
            "The classifier should share the chat-session task scope",
        )
        events = [
            event.get("name")
            for event in self.events_since(checkpoint)
            if event.get("data", {}).get("task_id") == result.task_id
        ]
        self.soft_assert(
            "session_map_classification_started" in events
            and "session_map_classification_completed" in events,
            "Classification should emit bounded start and completion telemetry",
        )

        class FailingClassifier:
            async def classify(self, _request: object) -> object:
                raise DecisionProviderError("deterministic classifier failure")

        failure_checkpoint = self.event_checkpoint()
        try:
            with patch(
                "core.memory.session_map.gate.build_decision_classifier",
                return_value=FailingClassifier(),
            ):
                await run_session_map_gate(
                    request,
                    authority=LOCAL_USER_AUTHORITY,
                )
        except DecisionProviderError:
            pass
        else:
            raise AssertionError("A classifier failure should escape the gate task")

        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_CLASSIFICATION.value
        )
        failed = tasks[-1]
        self.soft_assert_equal(
            failed.status,
            "failed",
            "A provider failure should fail the owning classification task",
        )
        self.soft_assert_equal(
            failed.terminal_error_type,
            "DecisionProviderError",
            "The task should retain the classifier failure category",
        )
        failure_events = [
            event.get("name")
            for event in self.events_since(failure_checkpoint)
            if event.get("data", {}).get("task_id") == failed.task_id
        ]
        self.soft_assert(
            "session_map_classification_failed" in failure_events,
            "A provider failure should emit classification failure telemetry",
        )

        self.assert_no_failures()


def _current_map() -> SessionMapDraft:
    return SessionMapDraft(
        entries=(
            SessionMapEntry(
                id="redevelopment_legal_review",
                kind="goal",
                state="active",
                basis="user_established",
                text="Review the redevelopment proposal with counsel.",
                sources=(SourceRange(start=4, end=5),),
            ),
        )
    )


def _envelope() -> CanonicalEvictionEnvelope:
    return CanonicalEvictionEnvelope(
        envelope_id="session-map-classification-envelope",
        session_id="session-map-classification",
        vault_name="SessionMapClassificationVault",
        history_revision=3,
        source_start_sequence_index=10,
        source_end_sequence_index=11,
        message_count=2,
        estimated_tokens=120,
        projected_text=(
            "[source:10] USER:\nThe lawyer accepted the engagement.\n\n"
            "[source:11] ASSISTANT:\nThe next step is substantive review."
        ),
        source_digest="session-map-classification-digest",
    )
