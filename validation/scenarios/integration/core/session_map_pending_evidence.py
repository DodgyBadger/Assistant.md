"""Validate one-way reconciliation of legacy deferred session-map evidence."""

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

from core.chat.compaction import (  # noqa: E402
    _run_stepped_session_map_reduction,
    build_canonical_evidence_range,
)
from core.constants import SESSION_MAP_CONTEXT_PROMPT_VERSION  # noqa: E402
from core.identity import (  # noqa: E402
    LOCAL_USER_PRINCIPAL_ID,
    ExecutionAuthority,
)
from core.memory.session_map.checkpoints import (  # noqa: E402
    SessionMapPendingEvidence,
    build_session_map_context_message,
    commit_session_map_context_checkpoint,
    load_session_map_pending_evidence,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SessionMapTrajectory,
    SourceRange,
)
from core.memory.session_map.readiness import (  # noqa: E402
    evaluate_session_map_compaction_readiness,
)
from core.runtime.execution_tasks import ExecutionTaskKind  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SessionMapPendingEvidenceScenario(BaseScenario):
    """Consume a legacy pending range in the next unconditional author pass."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SessionMapPendingEvidenceVault")
        await self.start_system()
        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_author_model", "test"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "1"),
            ("compaction_retained_turns", "1"),
            ("compaction_high_watermark_tokens", "2"),
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
        session_id = "session-map-pending-evidence"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        raw_messages = [
            _user("Develop the consulting offer."),
            _assistant("We will keep the offer practical."),
            _user("Preserve the legacy pending evidence."),
            _assistant("That pending evidence remains relevant."),
            _user("Create a durable getting-started note."),
            _assistant("The getting-started note now exists."),
            _user("Keep the current launch plan."),
            _assistant("The launch plan remains current."),
        ]
        store.add_messages(session_id, vault.name, raw_messages)

        initial_revision = store.get_session_history_revision(session_id, vault.name)
        initial_evidence = build_canonical_evidence_range(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            source_start_sequence_index=0,
            source_end_sequence_index=1,
            history_revision=initial_revision,
        )
        assert initial_evidence.status == "resolved"
        initial_map = SessionMapDraft(
            schema_version=1,
            entries=(
                SessionMapEntry(
                    id="consulting_offer",
                    kind="goal",
                    state="active",
                    basis="mixed",
                    text="Develop a practical consulting offer.",
                    sources=(SourceRange(start=0, end=1),),
                ),
            ),
        )
        commit_session_map_context_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            draft=initial_map,
            previous_map=SessionMapDraft(),
            new_evidence=initial_evidence.envelopes,
            expected_history_revision=initial_revision,
            message_count_before=len(raw_messages),
            source="validation",
            authoring_task_id="initial-author-task",
            checkpoint_id="initial-map",
        )

        deferred_revision = store.get_session_history_revision(session_id, vault.name)
        deferred_evidence = build_canonical_evidence_range(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            source_start_sequence_index=2,
            source_end_sequence_index=3,
            history_revision=deferred_revision,
        )
        assert deferred_evidence.status == "resolved"
        deferred_envelope = deferred_evidence.envelopes[0]
        pending = SessionMapPendingEvidence(
            start_sequence_index=deferred_envelope.source_start_sequence_index,
            end_sequence_index=deferred_envelope.source_end_sequence_index,
            source_digest=deferred_envelope.source_digest,
            estimated_tokens=deferred_envelope.estimated_tokens,
        )
        legacy_context = build_session_map_context_message(initial_map)
        store.add_context_checkpoint(
            session_id=session_id,
            vault_name=vault.name,
            checkpoint_id="legacy-deferred-map",
            checkpoint_kind="session_map",
            source="validation",
            message_count_before=7,
            last_message_sequence_index=3,
            summary_message=legacy_context,
            replacement_history=[legacy_context],
            metadata={
                "checkpoint_kind": "session_map",
                "prompt_contract_version": SESSION_MAP_CONTEXT_PROMPT_VERSION,
                "source_history_revision": deferred_revision,
                "consumed_through_sequence_index": 3,
                "map_observed_through_sequence_index": 1,
                "evidence_envelope_ids": [deferred_envelope.envelope_id],
                "map": initial_map.model_dump(mode="json"),
                "pending_evidence": {
                    "start_sequence_index": pending.start_sequence_index,
                    "end_sequence_index": pending.end_sequence_index,
                    "source_digest": pending.source_digest,
                    "estimated_tokens": pending.estimated_tokens,
                },
                "classification": {
                    "task_id": "legacy-classification",
                    "model_alias": "jev",
                    "score": 0.22,
                    "threshold": 0.5,
                    "prompt_contract_version": "session-map-gate-v1",
                    "action": "deferred",
                },
            },
            expected_history_revision=deferred_revision,
        )
        legacy_checkpoint = store.get_latest_context_checkpoint(session_id, vault.name)
        assert legacy_checkpoint is not None
        self.soft_assert_equal(
            load_session_map_pending_evidence(legacy_checkpoint),
            pending,
            "The compatibility fixture should carry only a canonical pending range",
        )
        self.soft_assert(
            "Preserve the legacy pending evidence"
            not in (legacy_checkpoint.metadata_json or ""),
            "Pending metadata must not duplicate canonical transcript text",
        )

        import core.memory.session_map.service as session_map_service

        observed_ranges: list[tuple[int, int]] = []

        async def authored_map(**kwargs: object) -> SessionMapDraft:
            payload = json.loads(str(kwargs["prompt"]))
            sources = tuple(
                SourceRange(**item["source_range"])
                for item in payload["new_evidence_envelopes"]
            )
            observed_ranges.extend((source.start, source.end) for source in sources)
            return SessionMapDraft(
                trajectory=SessionMapTrajectory(
                    text="The consulting offer advanced by reconciling a durable note with the current launch plan.",
                    sources=sources,
                ),
                entries=(
                    initial_map.entries[0],
                    SessionMapEntry(
                        id="reconciled_legacy_evidence",
                        kind="orientation",
                        state="active",
                        basis="mixed",
                        text="Legacy and newly evicted evidence were reconciled.",
                        sources=sources,
                    ),
                ),
            )

        readiness = evaluate_session_map_compaction_readiness(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
        )
        assert readiness.enabled
        original_author = session_map_service._invoke_session_map_model
        session_map_service._invoke_session_map_model = authored_map
        try:
            result = await _run_stepped_session_map_reduction(
                session_id=session_id,
                vault_name=vault.name,
                readiness=readiness,
                authority=ExecutionAuthority(LOCAL_USER_PRINCIPAL_ID),
                store=store,
            )
        finally:
            session_map_service._invoke_session_map_model = original_author

        assert result is not None
        self.soft_assert_equal(
            observed_ranges,
            [(2, 3), (4, 5)],
            "Unconditional authoring should receive pending and newly evicted evidence",
        )
        latest = store.get_latest_context_checkpoint(session_id, vault.name)
        assert latest is not None
        self.soft_assert_equal(
            load_session_map_pending_evidence(latest),
            None,
            "The authored checkpoint should clear legacy pending evidence",
        )
        latest_metadata = json.loads(latest.metadata_json or "{}")
        self.soft_assert(
            "classification" not in latest_metadata
            and "pending_evidence" not in latest_metadata,
            "New checkpoints should not persist retired gate state",
        )
        self.soft_assert_equal(
            store.get_history(session_id, vault.name, mode="raw"),
            raw_messages,
            "Reconciliation must preserve every canonical raw message",
        )
        self.soft_assert_equal(
            (store.get_history(session_id, vault.name) or [])[1:],
            raw_messages[6:],
            "Effective history should contain the new map plus the raw tail",
        )
        author_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        self.soft_assert_equal(
            [task.status for task in author_tasks],
            ["completed"],
            "Legacy reconciliation should use one governed author task",
        )
        all_tasks = await runtime.task_coordinator.list_tasks()
        self.soft_assert(
            all(task.kind != "session_map_classification" for task in all_tasks),
            "Legacy reconciliation should not dispatch a retired classifier task",
        )

        self.assert_no_failures()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
