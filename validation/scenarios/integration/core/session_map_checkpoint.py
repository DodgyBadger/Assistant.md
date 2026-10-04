"""Validate atomic session-map checkpoints and effective-history composition."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.chat.chat_store import ChatHistoryCorruptionError, ChatStore  # noqa: E402
from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.checkpoints import (  # noqa: E402
    build_session_map_context_message,
    commit_session_map_context_checkpoint,
    load_session_map_checkpoint,
    load_session_map_observed_through,
)
from core.memory.session_map.evidence import (  # noqa: E402
    SessionMapEvidence,
    SessionMapMessageEvidence,
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
        first_commit = commit_session_map_context_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            draft=first_map,
            previous_map=SessionMapDraft(),
            new_evidence=(first_envelope,),
            expected_history_revision=initial_revision,
            message_count_before=len(raw_messages),
            source="validation",
            authoring_task_id="task-first-map",
            authoring_prompt_version="eviction-map-v8",
            author_model_alias="gpt-mini",
            author_thinking="low",
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
        first_metadata = json.loads(first_commit.checkpoint.metadata_json or "{}")
        self.soft_assert_equal(
            {
                "map_schema_version": first_metadata.get("map_schema_version"),
                "authoring_prompt_version": first_metadata.get(
                    "authoring_prompt_version"
                ),
                "context_prompt_version": first_metadata.get("context_prompt_version"),
                "author_model_alias": first_metadata.get("author_model_alias"),
                "author_thinking": first_metadata.get("author_thinking"),
                "source_history_revision": first_metadata.get(
                    "source_history_revision"
                ),
                "evidence_source_start_sequence_index": first_metadata.get(
                    "evidence_source_start_sequence_index"
                ),
                "evidence_source_end_sequence_index": first_metadata.get(
                    "evidence_source_end_sequence_index"
                ),
                "map_observed_through_sequence_index": first_metadata.get(
                    "map_observed_through_sequence_index"
                ),
            },
            {
                "map_schema_version": 3,
                "authoring_prompt_version": "eviction-map-v8",
                "context_prompt_version": "session-map-context-v2",
                "author_model_alias": "gpt-mini",
                "author_thinking": "low",
                "source_history_revision": initial_revision,
                "evidence_source_start_sequence_index": 0,
                "evidence_source_end_sequence_index": 1,
                "map_observed_through_sequence_index": 1,
            },
            "Map checkpoints should preserve distinct authoring and context provenance",
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
            commit_session_map_context_checkpoint(
                store=store,
                session_id=session_id,
                vault_name=vault.name,
                draft=first_map,
                previous_map=first_map,
                new_evidence=(stale_envelope,),
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
            minimum_retained_groups=1,
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
        second_commit = commit_session_map_context_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault.name,
            draft=second_map,
            previous_map=first_map,
            new_evidence=(second_envelope,),
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
        self.soft_assert(
            "transcript" not in current_payload,
            "Map inspection should not duplicate the canonical chat timeline",
        )
        detail_response = self.call_api(
            f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
        )
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()
        detail_messages = detail_payload["messages"]
        self.soft_assert_equal(
            detail_payload["context_checkpoint_kind"],
            "session_map",
            "Session detail should identify the effective context boundary explicitly",
        )
        self.soft_assert_equal(
            detail_payload["context_checkpoint_id"],
            "second-map-checkpoint",
            "Session detail should link the context boundary to the inspectable checkpoint",
        )
        self.soft_assert_equal(
            [message["sequence_index"] for message in detail_messages],
            [0, 1, 2, 3, 4, 5],
            "A small mapped session should render complete canonical messages in the chat timeline",
        )
        paged_response = self.call_api(
            f"/api/chat/sessions/{session_id}/timeline?vault_name={vault.name}"
            "&page_size=2"
        )
        assert paged_response.status_code == 200
        paged_transcript = paged_response.json()
        self.soft_assert_equal(
            (
                paged_transcript["has_older"],
                paged_transcript["older_before_sequence_index"],
            ),
            (True, 4),
            "Canonical timeline paging should expose a stable reverse cursor",
        )
        self.soft_assert_equal(
            [message["sequence_index"] for message in paged_transcript["messages"]],
            [4, 5],
            "The newest canonical timeline page should remain chronological and bounded",
        )
        older_response = self.call_api(
            f"/api/chat/sessions/{session_id}/timeline?vault_name={vault.name}"
            "&page_size=2&before_sequence_index=4"
        )
        assert older_response.status_code == 200
        self.soft_assert_equal(
            [
                message["sequence_index"]
                for message in older_response.json()["messages"]
            ],
            [2, 3],
            "Loading older canonical messages should produce no duplicate or missing rows",
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
                    TextPart("<think>\n\n"),
                    ToolCallPart(
                        tool_name="file_read",
                        args={"operation": "read", "path": "plan.md"},
                        tool_call_id="call-map-transcript",
                    ),
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

        retry_session_id = "session-map-retry-transcript"
        store.ensure_session(
            retry_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            retry_session_id,
            vault.name,
            [
                _user("Run the requested operation."),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="file_read",
                            args={},
                            tool_call_id="call-map-retry",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        RetryPromptPart(
                            "The path is required.",
                            tool_name="file_read",
                            tool_call_id="call-map-retry",
                        )
                    ]
                ),
                _assistant("I need a path before I can continue."),
            ],
        )
        retry_boundaries, retry_has_older = store.get_canonical_display_rows_before(
            retry_session_id,
            vault.name,
            through_sequence_index=3,
            before_sequence_index=None,
            limit=10,
        )
        self.soft_assert_equal(
            (retry_boundaries, retry_has_older),
            (
                [
                    (0, 0, "message"),
                    (1, 3, "message"),
                ],
                False,
            ),
            "Named retry prompts should remain inside their complete assistant turn",
        )
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
        commit_session_map_context_checkpoint(
            store=store,
            session_id=tool_session_id,
            vault_name=vault.name,
            draft=tool_map,
            previous_map=SessionMapDraft(),
            new_evidence=(
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
            f"/api/chat/sessions/{tool_session_id}/timeline?vault_name={vault.name}"
            "&page_size=100"
        )
        assert tool_transcript_response.status_code == 200
        tool_transcript = tool_transcript_response.json()
        self.soft_assert_equal(
            [
                (message["sequence_index"], message["role"])
                for message in tool_transcript["messages"]
            ],
            [
                (0, "user"),
                (1, "assistant"),
                (4, "user"),
                (5, "assistant"),
            ],
            "The canonical chat timeline should keep each tool exchange with its assistant turn",
        )
        self.soft_assert(
            all(
                message["content"].strip()
                for message in tool_transcript["messages"]
                if message["role"] == "assistant"
            ),
            "Provider control markers must not produce empty standalone assistant rows",
        )
        self.soft_assert_equal(
            (
                tool_transcript["messages"][1]["through_sequence_index"],
                tool_transcript["messages"][1]["content"],
                tool_transcript["messages"][1]["tool_calls"],
            ),
            (
                3,
                "The planning file is current.",
                [
                    {
                        "tool_call_id": "call-map-transcript",
                        "tool_name": "file_read",
                        "status": "completed",
                        "token_count": 5,
                    }
                ],
            ),
            "The assistant turn should retain its answer and safe, inspectable tool summaries",
        )
        self.soft_assert_equal(
            [message["fork_sequence_index"] for message in tool_transcript["messages"]],
            [0, 3, 4, 5],
            "Canonical transcript turns should fork from their final safe sequence",
        )
        tool_page_response = self.call_api(
            f"/api/chat/sessions/{tool_session_id}/timeline?vault_name={vault.name}"
            "&before_sequence_index=3&page_size=1"
        )
        assert tool_page_response.status_code == 200
        tool_page = tool_page_response.json()
        self.soft_assert_equal(
            [
                (item["sequence_index"], item["through_sequence_index"])
                for item in tool_page["messages"]
            ],
            [(1, 3)],
            "One timeline page should retain a complete assistant turn",
        )
        with patch.object(
            ChatStore,
            "get_canonical_display_rows_before",
            return_value=([(1, 1, "message"), (2, 3, "message")], False),
        ):
            drift_response = self.call_api(
                f"/api/chat/sessions/{tool_session_id}/timeline?vault_name={vault.name}"
                "&page_size=2"
            )
        assert drift_response.status_code == 200, drift_response.text
        self.soft_assert_equal(
            [
                (item["sequence_index"], item["through_sequence_index"])
                for item in drift_response.json()["messages"]
            ],
            [(1, 1), (2, 3)],
            "A compact paging boundary should remain renderable when broader hydration would merge adjacent assistant rows",
        )
        with patch.object(
            ChatStore,
            "get_canonical_display_rows_before",
            return_value=([(1, 4, "message")], False),
        ):
            mismatch_response = self.call_api(
                f"/api/chat/sessions/{tool_session_id}/timeline?vault_name={vault.name}"
                "&page_size=1"
            )
        self.soft_assert_equal(
            (
                mismatch_response.status_code,
                mismatch_response.json().get("error"),
            ),
            (409, "CanonicalTimelineProjectionMismatch"),
            "An irreconcilable compact boundary should fail with a stable API contract",
        )
        projection_activity = self.call_api("/api/system/activity-log?limit=200")
        assert projection_activity.status_code == 200
        self.soft_assert(
            any(
                entry.get("data", {}).get("event")
                == "canonical_timeline_projection_failed"
                and entry["data"].get("status") == "failed"
                and entry["data"].get("session_id") == tool_session_id
                and entry["data"].get("boundary_start") == 1
                and entry["data"].get("boundary_end") == 4
                and entry["data"].get("boundary_kind") == "message"
                and entry["data"].get("error_type")
                == "CanonicalTimelineProjectionMismatch"
                for entry in projection_activity.json()["entries"]
            ),
            "System Activity should identify the exact irreconcilable transcript boundary",
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
            200,
            "Canonical timeline tool details should remain inspectable after compaction",
        )

        recovery_session_id = "corrupted-recovery-checkpoint"
        store.ensure_session(
            recovery_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(recovery_session_id, vault.name, raw_messages)
        recovery_context = _user("Persisted recovery context")
        store.add_compaction_checkpoint(
            session_id=recovery_session_id,
            vault_name=vault.name,
            checkpoint_id="corrupted-recovery-checkpoint",
            source="validation",
            message_count_before=len(raw_messages),
            last_message_sequence_index=3,
            summary_message=recovery_context,
            replacement_history=[recovery_context],
            replacement_source_sequence_indexes=[None],
        )
        for inspected_session_id in (session_id, recovery_session_id):
            self._assert_corrupt_replacement_is_rejected(
                store=store,
                session_id=inspected_session_id,
                vault_name=vault.name,
                database_path=get_runtime_context().config.system_root
                / "chat_sessions.db",
            )

        self._assert_checkpoint_boundary_contract(store=store, vault_name=vault.name)
        self.assert_no_failures()

    def _assert_checkpoint_boundary_contract(
        self, *, store: ChatStore, vault_name: str
    ) -> None:
        session_id = "checkpoint-boundary-integrity"
        store.ensure_session(
            session_id, vault_name, owner_principal_id=LOCAL_USER_PRINCIPAL_ID
        )
        raw_messages = [
            message
            for index in range(4)
            for message in (_user(f"Question {index}"), _assistant(f"Answer {index}"))
        ]
        store.add_messages(session_id, vault_name, raw_messages)
        revision = store.get_session_history_revision(session_id, vault_name)

        def envelope(start: int, end: int) -> SessionMapEvidence:
            return _envelope(
                session_id=session_id,
                vault_name=vault_name,
                history_revision=revision,
                start=start,
                end=end,
            )

        recent = (SessionMapMessageEvidence(3, "assistant", "Recent context"),)
        retrieved = (SessionMapMessageEvidence(7, "assistant", "Retrieved context"),)
        invalid_cases = [
            ((envelope(2, 3),), (), (), None),
            ((envelope(0, 1), envelope(3, 4)), (), (), None),
            ((envelope(0, 1),), (), (), -1),
            ((envelope(0, 1),), (), (), 0),
            ((envelope(0, 1),), (), (), True),
            ((envelope(0, 1),), recent, (), 1),
            ((envelope(0, 1),), (), retrieved, 3),
        ]
        for evidence, recent_evidence, retrieved_evidence, observed in invalid_cases:
            try:
                commit_session_map_context_checkpoint(
                    store=store,
                    session_id=session_id,
                    vault_name=vault_name,
                    draft=SessionMapDraft(),
                    previous_map=SessionMapDraft(),
                    new_evidence=evidence,
                    expected_history_revision=revision,
                    message_count_before=len(raw_messages),
                    source="validation",
                    recent_evidence=recent_evidence,
                    retrieved_evidence=retrieved_evidence,
                    map_observed_through_sequence_index=observed,
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "Invalid evidence coverage or observation cutoff must not commit"
                )
            self.soft_assert_equal(
                store.get_session_history_revision(session_id, vault_name),
                revision,
                "Rejected boundary evidence must not advance history revision",
            )
            self.soft_assert_equal(
                store.list_context_checkpoints(session_id, vault_name),
                [],
                "Rejected boundary evidence must not create a checkpoint",
            )

        committed = commit_session_map_context_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            draft=SessionMapDraft(),
            previous_map=SessionMapDraft(),
            new_evidence=(envelope(0, 1),),
            expected_history_revision=revision,
            message_count_before=len(raw_messages),
            source="validation",
            recent_evidence=recent,
            retrieved_evidence=retrieved,
        )
        self.soft_assert_equal(
            load_session_map_observed_through(committed.checkpoint),
            7,
            "The default cutoff must cover retained and retrieved authoring evidence",
        )
        revision = store.get_session_history_revision(session_id, vault_name)
        for start in (1, 3):
            try:
                commit_session_map_context_checkpoint(
                    store=store,
                    session_id=session_id,
                    vault_name=vault_name,
                    draft=SessionMapDraft(),
                    previous_map=SessionMapDraft(),
                    new_evidence=(envelope(start, 4),),
                    expected_history_revision=revision,
                    message_count_before=7,
                    source="validation",
                )
            except ValueError:
                pass
            else:
                raise AssertionError(
                    "Later evidence must begin after the prior boundary"
                )
        committed = commit_session_map_context_checkpoint(
            store=store,
            session_id=session_id,
            vault_name=vault_name,
            draft=SessionMapDraft(),
            previous_map=SessionMapDraft(),
            new_evidence=(envelope(2, 3),),
            expected_history_revision=revision,
            message_count_before=7,
            source="validation",
        )
        self.soft_assert_equal(
            load_session_map_observed_through(committed.checkpoint),
            7,
            "A rewrite must preserve the inherited map's observation cutoff",
        )
        self.soft_assert_equal(
            store.get_history(session_id, vault_name, mode="raw"),
            raw_messages,
            "Boundary validation and authoring must preserve canonical history",
        )

    def _assert_corrupt_replacement_is_rejected(
        self,
        *,
        store: ChatStore,
        session_id: str,
        vault_name: str,
        database_path: Path,
    ) -> None:
        checkpoint = store.get_latest_context_checkpoint(session_id, vault_name)
        assert checkpoint is not None
        raw_before = store.get_history(session_id, vault_name, mode="raw")
        effective_before = store.get_history(session_id, vault_name)
        revision_before = store.get_session_history_revision(session_id, vault_name)
        private_marker = "private-checkpoint-contents"
        with sqlite3.connect(database_path) as conn:
            conn.execute(
                """
                UPDATE chat_compaction_checkpoints
                SET replacement_history_json = ?
                WHERE checkpoint_id = ?
                """,
                (json.dumps({"private": private_marker}), checkpoint.checkpoint_id),
            )
        try:
            try:
                store.get_history(session_id, vault_name)
            except ChatHistoryCorruptionError as exc:
                self.soft_assert_equal(
                    (
                        exc.session_id,
                        exc.vault_name,
                        exc.checkpoint_id,
                    ),
                    (session_id, vault_name, checkpoint.checkpoint_id),
                    "A damaged checkpoint should identify its exact durable record",
                )
            else:
                self.soft_assert(
                    False,
                    "Damaged V1/V2 context must fail instead of continuing with only the raw tail",
                )
            self.soft_assert_equal(
                store.get_history(session_id, vault_name, mode="raw"),
                raw_before,
                "Rejecting a damaged checkpoint must preserve every canonical message",
            )
            self.soft_assert_equal(
                store.get_session_history_revision(session_id, vault_name),
                revision_before,
                "A failed effective-history read must not mutate session history",
            )
        finally:
            with sqlite3.connect(database_path) as conn:
                conn.execute(
                    """
                    UPDATE chat_compaction_checkpoints
                    SET replacement_history_json = ?
                    WHERE checkpoint_id = ?
                    """,
                    (checkpoint.replacement_history_json, checkpoint.checkpoint_id),
                )
        self.soft_assert_equal(
            store.get_history(session_id, vault_name),
            effective_before,
            "Restoring the persisted checkpoint should recover its complete effective context",
        )
        activity_response = self.call_api("/api/system/activity-log?limit=200")
        assert activity_response.status_code == 200
        entries = activity_response.json()["entries"]
        self.soft_assert(
            any(
                entry.get("data", {}).get("event")
                == "chat_history_deserialization_failed"
                and entry["data"].get("status") == "failed"
                and entry["data"].get("session_id") == session_id
                and entry["data"].get("vault_name") == vault_name
                and entry["data"].get("checkpoint_id") == checkpoint.checkpoint_id
                and entry["data"].get("error_type") == "ChatHistoryCorruptionError"
                for entry in entries
            ),
            "System Activity should identify each failed checkpoint without collapsing distinct issues",
        )
        self.soft_assert(
            private_marker not in json.dumps(entries),
            "Checkpoint failure diagnostics must not include persisted transcript contents",
        )


def _envelope(
    *,
    session_id: str,
    vault_name: str,
    history_revision: int,
    start: int,
    end: int,
) -> SessionMapEvidence:
    return SessionMapEvidence(
        evidence_id=f"map-envelope-{start}-{end}-r{history_revision}",
        session_id=session_id,
        vault_name=vault_name,
        history_revision=history_revision,
        source_start_sequence_index=start,
        source_end_sequence_index=end,
        message_count=end - start + 1,
        estimated_tokens=100,
        projected_text=f"Evidence {start}-{end}",
        source_digest=f"digest-{start}-{end}",
        citable_source_ranges=(SourceRange(start=start, end=end),),
    )


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
