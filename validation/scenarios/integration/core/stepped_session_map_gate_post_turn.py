"""Validate the optional cumulative movement gate in post-turn reduction."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from core.chat.compaction import maybe_auto_compact_after_turn  # noqa: E402
from core.identity import (  # noqa: E402
    LOCAL_USER_AUTHORITY,
    LOCAL_USER_PRINCIPAL_ID,
    use_execution_authority,
)
from core.llm.decision import (  # noqa: E402
    DecisionMetadata,
    DecisionProviderError,
    DecisionResult,
    DecisionUsage,
)
from core.memory.session_map.checkpoints import (  # noqa: E402
    load_session_map_pending_evidence,
)
from core.memory.session_map.gate import SessionMapMovementDecision  # noqa: E402
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SourceRange,
)
from core.runtime.execution_tasks import ExecutionTaskKind  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SteppedSessionMapGatePostTurnScenario(BaseScenario):
    """Gate later map rewrites without making classification authoritative."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SteppedSessionMapGatePostTurnVault")
        await self.start_system()
        for key, value in (
            ("context_reduction_strategy", "stepped_session_map"),
            ("session_map_author_model", "test"),
            ("session_map_author_thinking", "low"),
            ("session_map_low_watermark_tokens", "1"),
            ("session_map_gate_model", "jev"),
            ("session_map_gate_threshold", "0.5"),
            ("session_map_gate_max_input_tokens", "100000"),
            ("compaction_token_threshold", "2"),
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
        session_id = "stepped-session-map-gate-post-turn"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                _user("Establish the consulting offer."),
                _assistant("The offer is practical adoption guidance."),
                _user("Adopt a technology-governance-people triad."),
                _assistant("The triad is now part of the positioning."),
                _user("Keep the warm-network launch approach."),
                _assistant("The launch approach remains current."),
            ],
        )

        author_ranges: list[list[tuple[int, int]]] = []

        async def authored_map(**kwargs: object) -> SessionMapDraft:
            payload = json.loads(str(kwargs["prompt"]))
            ranges = [
                (item["source_range"]["start"], item["source_range"]["end"])
                for item in payload["new_evidence_envelopes"]
            ]
            author_ranges.append(ranges)
            previous = SessionMapDraft.model_validate(payload["previous_map"])
            prior_sources = previous.entries[0].sources if previous.entries else ()
            newest = SourceRange(start=ranges[-1][0], end=ranges[-1][1])
            sources = (
                prior_sources if newest in prior_sources else (*prior_sources, newest)
            )
            return SessionMapDraft(
                entries=(
                    SessionMapEntry(
                        id="consulting_offer_state",
                        kind="goal",
                        state="active",
                        basis="mixed",
                        text="Maintain the current practical consulting offer and launch plan.",
                        sources=sources,
                    ),
                )
            )

        classifier_calls = 0
        classifier_ranges: list[list[tuple[int, int]]] = []

        class SequenceClassifier:
            async def classify(self, request):
                nonlocal classifier_calls
                classifier_calls += 1
                payload = json.loads(request.state)
                classifier_ranges.append(
                    [
                        (
                            item["source_range"]["start"],
                            item["source_range"]["end"],
                        )
                        for item in payload["cumulative_new_evidence_envelopes"]
                    ]
                )
                if classifier_calls == 3:
                    raise DecisionProviderError("deterministic gate failure")
                score = 0.2 if classifier_calls == 1 else 0.8
                return DecisionResult(
                    output=SessionMapMovementDecision(
                        material_map_update_probability=score
                    ),
                    requested_model_alias="jev",
                    resolved_model_name="jev-latest",
                    provider_name="typesafe",
                    latency_seconds=0.01,
                    usage=DecisionUsage(
                        requests=1,
                        input_tokens=100,
                        output_tokens=4,
                    ),
                    metadata=DecisionMetadata(),
                )

        import core.memory.session_map.gate as gate_module
        import core.memory.session_map.service as authoring_module

        original_author = authoring_module._invoke_session_map_model
        original_classifier = gate_module.build_decision_classifier
        authoring_module._invoke_session_map_model = authored_map
        gate_module.build_decision_classifier = lambda _alias: SequenceClassifier()
        results = []
        try:
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                results.append(
                    await maybe_auto_compact_after_turn(
                        session_id=session_id,
                        vault_name=vault.name,
                        vault_path=str(vault),
                    )
                )

                store.add_messages(
                    session_id,
                    vault.name,
                    [_user("Try a minor wording change."), _assistant("Draft noted.")],
                )
                results.append(
                    await maybe_auto_compact_after_turn(
                        session_id=session_id,
                        vault_name=vault.name,
                        vault_path=str(vault),
                    )
                )
                deferred_checkpoint = store.get_latest_context_checkpoint(
                    session_id, vault.name
                )
                assert deferred_checkpoint is not None
                pending_after_deferral = load_session_map_pending_evidence(
                    deferred_checkpoint
                )

                store.add_messages(
                    session_id,
                    vault.name,
                    [
                        _user("Create the getting-started artifact."),
                        _assistant("The durable artifact now exists."),
                    ],
                )
                results.append(
                    await maybe_auto_compact_after_turn(
                        session_id=session_id,
                        vault_name=vault.name,
                        vault_path=str(vault),
                    )
                )
                authored_checkpoint = store.get_latest_context_checkpoint(
                    session_id, vault.name
                )
                assert authored_checkpoint is not None
                pending_after_authoring = load_session_map_pending_evidence(
                    authored_checkpoint
                )

                self.call_api(
                    "/api/system/settings/general/session_map_gate_max_input_tokens",
                    method="PUT",
                    data={"value": "1"},
                )
                store.add_messages(
                    session_id,
                    vault.name,
                    [_user("Record a forced update."), _assistant("Update recorded.")],
                )
                results.append(
                    await maybe_auto_compact_after_turn(
                        session_id=session_id,
                        vault_name=vault.name,
                        vault_path=str(vault),
                    )
                )

                self.call_api(
                    "/api/system/settings/general/session_map_gate_max_input_tokens",
                    method="PUT",
                    data={"value": "100000"},
                )
                store.add_messages(
                    session_id,
                    vault.name,
                    [
                        _user("Record an update despite gate failure."),
                        _assistant("Failure bypass update recorded."),
                    ],
                )
                results.append(
                    await maybe_auto_compact_after_turn(
                        session_id=session_id,
                        vault_name=vault.name,
                        vault_path=str(vault),
                    )
                )
        finally:
            authoring_module._invoke_session_map_model = original_author
            gate_module.build_decision_classifier = original_classifier

        self.soft_assert_equal(
            [result.action if result else None for result in results],
            ["authored", "deferred", "authored", "authored", "authored"],
            "Post-turn reduction should follow initial, gated, forced, and bypass actions",
        )
        self.soft_assert_equal(
            author_ranges,
            [[(0, 1), (2, 3)], [(4, 5), (6, 7)], [(8, 9)], [(10, 11)]],
            "Authoring should skip the low-score boundary and later receive cumulative evidence",
        )
        self.soft_assert_equal(
            classifier_ranges,
            [[(4, 5)], [(4, 5), (6, 7)], [(10, 11)]],
            "Classification should accumulate deferred evidence, skip forced input, and resume later",
        )
        self.soft_assert(
            pending_after_deferral is not None,
            "A low gate score should persist cumulative pending evidence",
        )
        self.soft_assert_equal(
            pending_after_authoring,
            None,
            "A later authored map should clear pending evidence",
        )
        self.soft_assert_equal(
            len(
                store.list_context_checkpoints(
                    session_id,
                    vault.name,
                    checkpoint_kind="session_map",
                )
            ),
            5,
            "Every reduction action should remain append-only and auditable",
        )
        self.soft_assert_equal(
            len(store.get_history(session_id, vault.name, mode="raw") or []),
            14,
            "Every gate action should preserve the canonical transcript",
        )

        classification_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_CLASSIFICATION.value
        )
        self.soft_assert_equal(
            [task.status for task in classification_tasks],
            ["completed", "completed", "failed"],
            "Only dispatched classifications should create tasks with visible outcomes",
        )
        authoring_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        self.soft_assert_equal(
            [task.status for task in authoring_tasks],
            ["completed", "completed", "completed", "completed"],
            "Initial, threshold, forced, and failure-bypass authors should all be governed",
        )
        recovery_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        self.soft_assert_equal(
            recovery_tasks,
            [],
            "A classifier failure alone must not trigger recovery-card fallback",
        )

        self.assert_no_failures()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
