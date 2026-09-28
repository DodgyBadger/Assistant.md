"""Canonical provenance contract for stepped eviction evidence."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from validation.core.base_scenario import BaseScenario


class SteppedEvictionEnvelopesScenario(BaseScenario):
    """Resolve safe plans to stable canonical ranges and reject synthetic history."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SteppedEvictionEnvelopesVault")
        await self.start_system()

        import core.chat.compaction as compaction
        from core.identity import LOCAL_USER_PRINCIPAL_ID
        from core.runtime.state import get_runtime_context

        store = get_runtime_context().chat_store
        session_id = "canonical_eviction_envelopes"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        messages = [
            _user("Initial question."),
            _assistant("Initial answer."),
            _user("Abandoned but still canonical request."),
            _user("Use the evidence tool."),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="evidence_probe",
                        args={},
                        tool_call_id="evidence-1",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="evidence_probe",
                        content="Canonical tool evidence.",
                        tool_call_id="evidence-1",
                    )
                ]
            ),
            _assistant("The evidence was incorporated."),
            _user("Newest question."),
            _assistant("Newest answer."),
        ]
        store.add_messages(session_id, vault.name, messages)
        persisted_history = store.get_history(session_id, vault.name) or []
        total_tokens = compaction.estimate_history_tokens(persisted_history)
        recent_tokens = compaction.estimate_history_tokens(persisted_history[-2:])
        plan = compaction.plan_stepped_history_eviction(
            persisted_history,
            high_watermark_tokens=total_tokens - 1,
            low_watermark_tokens=recent_tokens,
            history_revision=store.get_session_history_revision(session_id, vault.name),
        )

        resolved = compaction.build_canonical_eviction_envelopes(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            plan=plan,
        )
        assert resolved.status == "resolved"
        assert resolved.reason == "canonical_ranges_resolved"
        assert len(resolved.envelopes) == 3
        assert [
            (
                envelope.source_start_sequence_index,
                envelope.source_end_sequence_index,
            )
            for envelope in resolved.envelopes
        ] == [(0, 1), (2, 2), (3, 6)]
        assert resolved.envelopes[1].message_count == 1
        assert (
            resolved.envelopes[1].projected_text.count(
                "Abandoned but still canonical request."
            )
            == 1
        )
        assert "[source:5] USER:" in resolved.envelopes[2].projected_text
        assert "Canonical tool evidence." in resolved.envelopes[2].projected_text
        assert all(envelope.source_digest for envelope in resolved.envelopes)

        repeated = compaction.build_canonical_eviction_envelopes(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            plan=plan,
        )
        assert [envelope.envelope_id for envelope in repeated.envelopes] == [
            envelope.envelope_id for envelope in resolved.envelopes
        ], "Repeated resolution of the same canonical ranges should be idempotent"

        store.add_messages(
            session_id,
            vault.name,
            [_user("Later question."), _assistant("Later answer.")],
        )
        stale = compaction.build_canonical_eviction_envelopes(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            plan=plan,
        )
        assert stale.status == "unavailable"
        assert stale.reason == "stale_history_revision"

        current_history = store.get_history(session_id, vault.name) or []
        current_plan = compaction.plan_stepped_history_eviction(
            current_history,
            high_watermark_tokens=compaction.estimate_history_tokens(current_history)
            - 1,
            low_watermark_tokens=compaction.estimate_history_tokens(
                current_history[-2:]
            ),
            history_revision=store.get_session_history_revision(session_id, vault.name),
        )
        current = compaction.build_canonical_eviction_envelopes(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            plan=current_plan,
        )
        assert current.status == "resolved"
        assert [
            envelope.envelope_id
            for envelope in current.envelopes[: len(resolved.envelopes)]
        ] == [
            envelope.envelope_id for envelope in resolved.envelopes
        ], "Envelope identity should not change when later messages advance the revision"

        compacted_session_id = "synthetic_eviction_envelopes"
        store.ensure_session(
            compacted_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        compacted_messages = [
            _user("Older compacted question."),
            _assistant("Older compacted answer."),
            _user("Retained question."),
            _assistant("Retained answer."),
        ]
        store.add_messages(compacted_session_id, vault.name, compacted_messages)
        pre_checkpoint_plan = compaction.plan_stepped_history_eviction(
            compacted_messages,
            high_watermark_tokens=compaction.estimate_history_tokens(compacted_messages)
            - 1,
            low_watermark_tokens=compaction.estimate_history_tokens(
                compacted_messages[-2:]
            ),
            history_revision=store.get_session_history_revision(
                compacted_session_id, vault.name
            ),
        )
        summary = compaction.build_compaction_summary_message("Synthetic summary.")
        store.add_compaction_checkpoint(
            session_id=compacted_session_id,
            vault_name=vault.name,
            checkpoint_id="synthetic-checkpoint",
            source="validation",
            message_count_before=len(compacted_messages),
            last_message_sequence_index=3,
            summary_message=summary,
            replacement_history=[summary, *compacted_messages[-2:]],
        )
        synthetic = compaction.build_canonical_eviction_envelopes(
            store=store,
            session_id=compacted_session_id,
            vault_name=vault.name,
            plan=pre_checkpoint_plan,
        )
        assert synthetic.status == "unavailable"
        assert synthetic.reason == "synthetic_compaction_history"

        self.assert_no_failures()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
