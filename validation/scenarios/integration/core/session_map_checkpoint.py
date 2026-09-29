"""Validate atomic session-map checkpoints and effective-history composition."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
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
    SessionMapTrajectory,
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
            trajectory=SessionMapTrajectory(
                text="The session began by organizing a legal review of the proposal.",
                sources=(SourceRange(start=0, end=1),),
            ),
            entries=(
                SessionMapEntry(
                    id="proposal_legal_review",
                    kind="goal",
                    state="active",
                    basis="user_established",
                    text="Review the redevelopment proposal.",
                    sources=(SourceRange(start=0, end=1),),
                ),
            ),
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
        import core.chat.compaction as compaction

        second_plan = compaction.plan_stepped_history_eviction(
            effective_after_first,
            high_watermark_tokens=(
                compaction.estimate_history_tokens(effective_after_first) - 1
            ),
            low_watermark_tokens=compaction.estimate_history_tokens(
                [effective_after_first[0], *effective_after_first[-2:]]
            ),
            history_revision=second_revision,
            retained_prefix_count=1,
        )
        second_evidence = compaction.build_canonical_eviction_envelopes(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            plan=second_plan,
        )
        self.soft_assert_equal(
            second_evidence.status,
            "resolved",
            "A pinned map should still resolve later evictions to canonical evidence",
        )
        self.soft_assert_equal(
            [
                (
                    envelope.source_start_sequence_index,
                    envelope.source_end_sequence_index,
                )
                for envelope in second_evidence.envelopes
            ],
            [(2, 3)],
            "The pinned map must never be included in new evidence ranges",
        )
        second_envelope = second_evidence.envelopes[0]
        second_map = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The proposal review narrowed to the legal implications of City land ownership.",
                sources=(
                    SourceRange(start=0, end=1),
                    SourceRange(start=2, end=3),
                ),
            ),
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
            ),
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

        sessions_response = self.call_api(f"/api/chat/sessions?vault_name={vault.name}")
        assert sessions_response.status_code == 200
        listed_session = next(
            item
            for item in sessions_response.json()
            if item["session_id"] == session_id
        )
        self.soft_assert_equal(
            listed_session["has_session_map"],
            True,
            "Session discovery should expose the map inspection affordance",
        )
        current_response = self.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name={vault.name}"
        )
        assert current_response.status_code == 200
        current_payload = current_response.json()
        self.soft_assert_equal(
            current_payload["selected_checkpoint_id"],
            "second-map-checkpoint",
            "Map inspection should default to the latest checkpoint",
        )
        self.soft_assert_equal(
            [item["checkpoint_id"] for item in current_payload["revisions"]],
            ["first-map-checkpoint", "second-map-checkpoint"],
            "Map inspection should expose append-only checkpoint history",
        )
        self.soft_assert_equal(
            current_payload["revisions"][-1]["map_observed_through_sequence_index"],
            3,
            "The inspection payload should distinguish the authored observation boundary",
        )
        assert second_map.trajectory is not None
        self.soft_assert_equal(
            current_payload["session_map"]["trajectory"],
            second_map.trajectory.model_dump(mode="json"),
            "Map inspection should expose the source-linked narrative bridge",
        )
        self.soft_assert_equal(
            [
                message["sequence_index"]
                for message in current_payload["transcript"]["messages"]
            ],
            [0, 1, 2, 3],
            "Map inspection should expose bounded canonical history through the selected eviction boundary",
        )
        detail_response = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
        )
        assert detail_response.status_code == 200
        detail_messages = detail_response.json()["messages"]
        self.soft_assert_equal(
            detail_messages[0]["context_checkpoint_kind"],
            "session_map",
            "Session detail should identify the effective map replacement explicitly",
        )
        self.soft_assert_equal(
            detail_messages[0]["context_checkpoint_id"],
            "second-map-checkpoint",
            "Session detail should link the replacement row to the inspectable checkpoint",
        )
        self.soft_assert_equal(
            detail_messages[0]["content"],
            "",
            "Session detail should not project internal map JSON as chat display prose",
        )
        paged_response = self.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name={vault.name}"
            "&checkpoint_id=second-map-checkpoint&message_page=2&message_page_size=2"
        )
        assert paged_response.status_code == 200
        paged_transcript = paged_response.json()["transcript"]
        self.soft_assert_equal(
            (
                paged_transcript["page"],
                paged_transcript["page_count"],
                paged_transcript["total_entries"],
                paged_transcript["has_previous"],
                paged_transcript["has_next"],
            ),
            (2, 2, 4, True, False),
            "Canonical checkpoint transcript paging should expose stable boundaries",
        )
        self.soft_assert_equal(
            [message["sequence_index"] for message in paged_transcript["messages"]],
            [2, 3],
            "Canonical checkpoint transcript pages should remain chronological and bounded",
        )
        historical_response = self.call_api(
            f"/api/chat/sessions/{session_id}/map?vault_name={vault.name}"
            "&checkpoint_id=first-map-checkpoint"
        )
        assert historical_response.status_code == 200
        self.soft_assert_equal(
            historical_response.json()["session_map"],
            first_map.model_dump(mode="json"),
            "A historical checkpoint should return its original typed map",
        )
        self.soft_assert_equal(
            [
                message["sequence_index"]
                for message in historical_response.json()["transcript"]["messages"]
            ],
            [0, 1],
            "Historical checkpoint inspection should stop at that checkpoint's eviction boundary",
        )

        tool_session_id = "session-map-tool-transcript"
        store.ensure_session(
            tool_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        tool_messages = [
            _user("Inspect the planning file."),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="file_read",
                        args={"operation": "read", "path": "plan.md"},
                        tool_call_id="call-map-transcript",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="file_read",
                        content="Planning evidence.",
                        tool_call_id="call-map-transcript",
                    )
                ]
            ),
            _assistant("The planning file is current."),
            _user("Continue with the next step."),
            _assistant("I will continue."),
        ]
        store.add_messages(tool_session_id, vault.name, tool_messages)
        store.add_tool_event(
            session_id=tool_session_id,
            vault_name=vault.name,
            tool_call_id="call-map-transcript",
            tool_name="file_read",
            event_type="call",
            args={"operation": "read", "path": "plan.md"},
        )
        store.add_tool_event(
            session_id=tool_session_id,
            vault_name=vault.name,
            tool_call_id="call-map-transcript",
            tool_name="file_read",
            event_type="result",
            result_text="Planning evidence.",
            result_metadata={"status": "completed", "token_count": 5},
        )
        tool_revision = store.get_session_history_revision(tool_session_id, vault.name)
        tool_map = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The session inspected the planning file.",
                sources=(SourceRange(start=0, end=3),),
            ),
            entries=(
                SessionMapEntry(
                    id="planning_file_review",
                    kind="artifact",
                    state="active",
                    basis="mixed",
                    text="The planning file was inspected and is current.",
                    sources=(SourceRange(start=0, end=3),),
                ),
            ),
        )
        commit_session_map_checkpoint(
            store=store,
            session_id=tool_session_id,
            vault_name=vault.name,
            draft=tool_map,
            previous_map=SessionMapDraft(),
            envelopes=(
                _envelope(
                    session_id=tool_session_id,
                    vault_name=vault.name,
                    history_revision=tool_revision,
                    start=0,
                    end=3,
                ),
            ),
            expected_history_revision=tool_revision,
            message_count_before=len(tool_messages),
            source="validation",
            checkpoint_id="tool-transcript-checkpoint",
        )
        tool_transcript_response = self.call_api(
            f"/api/chat/sessions/{tool_session_id}/map?vault_name={vault.name}"
        )
        assert tool_transcript_response.status_code == 200
        tool_transcript = tool_transcript_response.json()["transcript"]
        self.soft_assert_equal(
            [
                (message["sequence_index"], message["role"])
                for message in tool_transcript["messages"]
            ],
            [(0, "user"), (1, "tool"), (3, "assistant")],
            "Map transcript inspection should preserve collapsed tool activity in sequence",
        )
        self.soft_assert_equal(
            (
                tool_transcript["messages"][1]["through_sequence_index"],
                tool_transcript["messages"][1]["tool_calls"],
            ),
            (
                2,
                [
                    {
                        "tool_call_id": "call-map-transcript",
                        "tool_name": "file_read",
                        "status": "completed",
                        "token_count": 5,
                    }
                ],
            ),
            "Collapsed tool activity should expose only safe, inspectable summaries",
        )
        scoped_tool_response = self.call_api(
            f"/api/chat/sessions/{tool_session_id}/tools/call-map-transcript"
            f"?vault_name={vault.name}&checkpoint_id=tool-transcript-checkpoint"
        )
        assert scoped_tool_response.status_code == 200
        self.soft_assert_equal(
            (
                scoped_tool_response.json()["args"],
                scoped_tool_response.json()["result_text"],
            ),
            (
                {"operation": "read", "path": "plan.md"},
                "Planning evidence.",
            ),
            "Checkpoint-scoped inspection should reuse complete tool details",
        )
        active_tool_response = self.call_api(
            f"/api/chat/sessions/{tool_session_id}/tools/call-map-transcript"
            f"?vault_name={vault.name}"
        )
        self.soft_assert_equal(
            active_tool_response.status_code,
            404,
            "Evicted tool details should require an authorized checkpoint scope",
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
