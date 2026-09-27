"""Validate atomic session-map checkpoints and effective-history composition."""

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

from core.chat.compaction import CanonicalEvictionEnvelope  # noqa: E402
from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.checkpoints import (  # noqa: E402
    build_session_map_context_message,
    commit_session_map_checkpoint,
    load_session_map_checkpoint,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SourceRange,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from core.utils.messages import extract_role_and_text  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SessionMapCheckpointScenario(BaseScenario):
    """Keep map state, eviction boundary, and effective context atomic."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SessionMapCheckpointVault")
        await self.start_system()
        store = get_runtime_context().chat_store
        session_id = "session-map-checkpoint"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        raw_messages = [
            _user("Review the proposal."),
            _assistant("I will organize the issues."),
            _user("The City owns the land."),
            _assistant("That ownership needs legal clarification."),
            _user("Draft questions for counsel."),
            _assistant("I drafted the questions."),
        ]
        store.add_messages(session_id, vault.name, raw_messages)
        initial_revision = store.get_session_history_revision(session_id, vault.name)
        first_envelope = _envelope(
            session_id=session_id,
            vault_name=vault.name,
            history_revision=initial_revision,
            start=0,
            end=1,
        )
        first_map = SessionMapDraft(
            entries=(
                SessionMapEntry(
                    id="proposal_legal_review",
                    kind="goal",
                    state="active",
                    basis="user_established",
                    text="Review the redevelopment proposal.",
                    sources=(SourceRange(start=0, end=1),),
                ),
            )
        )
        first_commit = commit_session_map_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            draft=first_map,
            previous_map=SessionMapDraft(),
            envelopes=(first_envelope,),
            expected_history_revision=initial_revision,
            message_count_before=len(raw_messages),
            source="validation",
            authoring_task_id="task-first-map",
            checkpoint_id="first-map-checkpoint",
        )

        self.soft_assert_equal(
            first_commit.checkpoint.checkpoint_kind,
            "session_map",
            "The durable checkpoint should be explicitly classified as a map",
        )
        self.soft_assert_equal(
            first_commit.checkpoint.last_message_sequence_index,
            1,
            "The checkpoint boundary should end at the last consumed message",
        )
        self.soft_assert_equal(
            load_session_map_checkpoint(first_commit.checkpoint),
            first_map,
            "The typed map should round-trip from checkpoint metadata",
        )
        self.soft_assert_equal(
            store.get_history(session_id, vault.name, mode="raw"),
            raw_messages,
            "Map checkpointing must not mutate canonical raw messages",
        )
        effective_after_first = store.get_history(session_id, vault.name) or []
        self.soft_assert_equal(
            len(effective_after_first),
            5,
            "Effective history should contain one map plus the unconsumed raw tail",
        )
        self.soft_assert_equal(
            effective_after_first[1:],
            raw_messages[2:],
            "Effective history should retain raw messages after the map boundary",
        )
        self.soft_assert_equal(
            extract_role_and_text(effective_after_first[0]),
            extract_role_and_text(build_session_map_context_message(first_map)),
            "Effective history should begin with the committed map context",
        )

        stale_envelope = _envelope(
            session_id=session_id,
            vault_name=vault.name,
            history_revision=initial_revision,
            start=2,
            end=3,
        )
        try:
            commit_session_map_checkpoint(
                store=store,
                session_id=session_id,
                vault_name=vault.name,
                draft=first_map,
                previous_map=first_map,
                envelopes=(stale_envelope,),
                expected_history_revision=initial_revision,
                message_count_before=len(effective_after_first),
                source="validation",
                checkpoint_id="stale-map-checkpoint",
            )
        except ValueError as exc:
            self.soft_assert(
                "history revision changed" in str(exc),
                "A stale commit should report its revision conflict",
            )
        else:
            raise AssertionError("A stale session-map checkpoint should be rejected")
        self.soft_assert_equal(
            len(
                store.list_context_checkpoints(
                    session_id,
                    vault.name,
                    checkpoint_kind="session_map",
                )
            ),
            1,
            "A stale write must not append a partial checkpoint",
        )
        self.soft_assert_equal(
            store.get_history(session_id, vault.name),
            effective_after_first,
            "A stale write must not change effective history",
        )

        second_revision = store.get_session_history_revision(session_id, vault.name)
        second_envelope = _envelope(
            session_id=session_id,
            vault_name=vault.name,
            history_revision=second_revision,
            start=2,
            end=3,
        )
        second_map = SessionMapDraft(
            entries=(
                first_map.entries[0],
                SessionMapEntry(
                    id="city_land_authority",
                    kind="constraint",
                    state="active",
                    basis="mixed",
                    text="City land ownership requires legal clarification.",
                    sources=(SourceRange(start=2, end=3),),
                ),
            )
        )
        second_commit = commit_session_map_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            draft=second_map,
            previous_map=first_map,
            envelopes=(second_envelope,),
            expected_history_revision=second_revision,
            message_count_before=len(effective_after_first),
            source="validation",
            checkpoint_id="second-map-checkpoint",
        )
        effective_after_second = store.get_history(session_id, vault.name) or []
        self.soft_assert_equal(
            effective_after_second[1:],
            raw_messages[4:],
            "A later checkpoint should retain only raw messages after its boundary",
        )
        self.soft_assert_equal(
            extract_role_and_text(effective_after_second[0]),
            extract_role_and_text(build_session_map_context_message(second_map)),
            "A later checkpoint should replace the prior map context",
        )
        checkpoints = store.list_context_checkpoints(
            session_id,
            vault.name,
            checkpoint_kind="session_map",
        )
        self.soft_assert_equal(
            [checkpoint.checkpoint_id for checkpoint in checkpoints],
            ["first-map-checkpoint", "second-map-checkpoint"],
            "Map checkpoints should remain append-only for audit",
        )
        self.soft_assert_equal(
            [load_session_map_checkpoint(checkpoint) for checkpoint in checkpoints],
            [first_map, second_map],
            "Every historical checkpoint should retain its typed map revision",
        )
        self.soft_assert_equal(
            second_commit.checkpoint.last_message_sequence_index,
            3,
            "The second map should consume exactly its new canonical evidence",
        )
        self.soft_assert_equal(
            store.get_history(session_id, vault.name, mode="raw"),
            raw_messages,
            "Repeated map checkpoints must preserve the complete raw transcript",
        )

        self.assert_no_failures()


def _envelope(
    *,
    session_id: str,
    vault_name: str,
    history_revision: int,
    start: int,
    end: int,
) -> CanonicalEvictionEnvelope:
    return CanonicalEvictionEnvelope(
        envelope_id=f"map-envelope-{start}-{end}-r{history_revision}",
        session_id=session_id,
        vault_name=vault_name,
        history_revision=history_revision,
        source_start_sequence_index=start,
        source_end_sequence_index=end,
        message_count=end - start + 1,
        estimated_tokens=100,
        projected_text=f"Evidence {start}-{end}",
        source_digest=f"digest-{start}-{end}",
    )


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
