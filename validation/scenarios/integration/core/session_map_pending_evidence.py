"""Validate durable cumulative evidence across deferred map rewrites."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from core.chat.compaction import build_canonical_evidence_range  # noqa: E402
from core.constants import SESSION_MAP_GATE_PROMPT_VERSION  # noqa: E402
from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.checkpoints import (  # noqa: E402
    SessionMapCheckpointDecision,
    SessionMapPendingEvidence,
    commit_session_map_checkpoint,
    load_session_map_checkpoint,
    load_session_map_pending_evidence,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SourceRange,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SessionMapPendingEvidenceScenario(BaseScenario):
    """Advance context while retaining skipped evidence as canonical references."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SessionMapPendingEvidenceVault")
        await self.start_system()
        store = get_runtime_context().chat_store
        session_id = "session-map-pending-evidence"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        raw_messages = [
            _user("Develop the consulting offer."),
            _assistant("We will keep the offer practical."),
            _user("Try the phrase deliberately forgettable pending evidence alpha."),
            _assistant("That wording is only a transient draft."),
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
            entries=(
                SessionMapEntry(
                    id="consulting_offer",
                    kind="goal",
                    state="active",
                    basis="mixed",
                    text="Develop a practical consulting offer.",
                    sources=(SourceRange(start=0, end=1),),
                ),
            )
        )
        commit_session_map_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            draft=initial_map,
            previous_map=SessionMapDraft(),
            envelopes=initial_evidence.envelopes,
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
        deferred = commit_session_map_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            draft=initial_map,
            previous_map=initial_map,
            envelopes=deferred_evidence.envelopes,
            expected_history_revision=deferred_revision,
            message_count_before=7,
            source="validation",
            pending_evidence=pending,
            classification=SessionMapCheckpointDecision(
                task_id="classification-deferred",
                model_alias="jev",
                score=0.22,
                threshold=0.5,
                prompt_contract_version=SESSION_MAP_GATE_PROMPT_VERSION,
                action="deferred",
            ),
            checkpoint_id="deferred-map",
        )
        self.soft_assert_equal(
            load_session_map_checkpoint(deferred.checkpoint),
            initial_map,
            "A deferred rewrite should carry the map forward unchanged",
        )
        self.soft_assert_equal(
            load_session_map_pending_evidence(deferred.checkpoint),
            pending,
            "A deferred checkpoint should retain only the bounded canonical reference",
        )
        self.soft_assert(
            "deliberately forgettable pending evidence alpha"
            not in (deferred.checkpoint.metadata_json or ""),
            "Pending checkpoint metadata must not duplicate canonical transcript text",
        )
        self.soft_assert_equal(
            (store.get_history(session_id, vault.name) or [])[1:],
            raw_messages[4:],
            "A deferred checkpoint should advance effective history safely",
        )

        appended = [
            _user("Also record the relationship-led launch decision."),
            _assistant("The relationship-led launch decision is recorded."),
        ]
        store.add_messages(session_id, vault.name, appended)
        authored_revision = store.get_session_history_revision(session_id, vault.name)
        rehydrated = build_canonical_evidence_range(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            source_start_sequence_index=pending.start_sequence_index,
            source_end_sequence_index=pending.end_sequence_index,
            history_revision=authored_revision,
            expected_source_digest=pending.source_digest,
        )
        new_evidence = build_canonical_evidence_range(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            source_start_sequence_index=4,
            source_end_sequence_index=5,
            history_revision=authored_revision,
        )
        self.soft_assert_equal(
            (rehydrated.status, new_evidence.status),
            ("resolved", "resolved"),
            "Pending and new evidence should resolve under the same current revision",
        )
        final_map = SessionMapDraft(
            entries=(
                initial_map.entries[0],
                SessionMapEntry(
                    id="getting_started_note",
                    kind="artifact",
                    state="active",
                    basis="mixed",
                    text="A durable getting-started note exists.",
                    sources=(SourceRange(start=4, end=5),),
                ),
            )
        )
        final = commit_session_map_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            draft=final_map,
            previous_map=initial_map,
            envelopes=(*rehydrated.envelopes, *new_evidence.envelopes),
            expected_history_revision=authored_revision,
            message_count_before=7,
            source="validation",
            authoring_task_id="later-author-task",
            classification=SessionMapCheckpointDecision(
                task_id="classification-authored",
                model_alias="jev",
                score=0.81,
                threshold=0.5,
                prompt_contract_version=SESSION_MAP_GATE_PROMPT_VERSION,
                action="authored",
            ),
            checkpoint_id="authored-map",
        )
        self.soft_assert_equal(
            final.checkpoint.last_message_sequence_index,
            5,
            "Later authoring should incorporate pending and newly evicted evidence",
        )
        self.soft_assert_equal(
            load_session_map_pending_evidence(final.checkpoint),
            None,
            "Successful whole-map authoring should clear pending evidence",
        )
        self.soft_assert_equal(
            store.get_history(session_id, vault.name, mode="raw"),
            [*raw_messages, *appended],
            "Deferred and authored checkpoints must preserve canonical raw history",
        )
        self.soft_assert_equal(
            (store.get_history(session_id, vault.name) or [])[1:],
            [*raw_messages[6:], *appended],
            "Final effective history should contain the new map plus the raw tail",
        )

        mismatched = build_canonical_evidence_range(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            source_start_sequence_index=pending.start_sequence_index,
            source_end_sequence_index=pending.end_sequence_index,
            history_revision=store.get_session_history_revision(session_id, vault.name),
            expected_source_digest="0" * 64,
        )
        self.soft_assert_equal(
            mismatched.reason,
            "source_digest_mismatch",
            "Cumulative evidence rehydration should fail closed on digest drift",
        )
        self.soft_assert_equal(
            [
                load_session_map_pending_evidence(checkpoint)
                for checkpoint in store.list_context_checkpoints(
                    session_id,
                    vault.name,
                    checkpoint_kind="session_map",
                )
            ],
            [None, pending, None],
            "Append-only checkpoints should retain the pending-evidence audit trail",
        )

        self.assert_no_failures()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
