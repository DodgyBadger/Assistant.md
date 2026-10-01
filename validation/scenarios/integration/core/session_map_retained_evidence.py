"""Validate citable retained evidence for stepped session-map authoring."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from core.chat.compaction import (  # noqa: E402
    SteppedHistoryEvictionPlan,
    _build_retained_session_map_evidence,
    _build_retrieved_session_map_evidence,
    maybe_auto_compact_after_turn,
)
from core.identity import (  # noqa: E402
    LOCAL_USER_AUTHORITY,
    LOCAL_USER_PRINCIPAL_ID,
    use_execution_authority,
)
from core.memory.session_map.checkpoints import (  # noqa: E402
    load_session_map_checkpoint,
)
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SessionMapTrajectory,
    SourceRange,
)
from core.runtime.execution_tasks import ExecutionTaskKind  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SessionMapRetainedEvidenceScenario(BaseScenario):
    """Keep retained completions out of evidence while preventing stale state."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SessionMapRetainedEvidenceVault")
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
        session_id = "session-map-retained-evidence"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                _user("Create the deployment README."),
                _assistant("The active next action is to create the README."),
                _user("The deployment README is now complete."),
                _assistant("Confirmed; no README work remains."),
            ],
        )

        captured_payloads: list[dict[str, object]] = []

        retained_artifact = SessionMapDraft(
            trajectory=SessionMapTrajectory(
                text="The deployment work moved from planning the README to completing it.",
                sources=(SourceRange(start=0, end=2),),
            ),
            entries=(
                SessionMapEntry(
                    id="deployment_readme",
                    kind="artifact",
                    state="active",
                    basis="user_established",
                    text="The deployment README is complete.",
                    sources=(SourceRange(start=2, end=2),),
                ),
            ),
        )

        async def author_without_stale_action(**kwargs: object) -> SessionMapDraft:
            payload = json.loads(str(kwargs["prompt"]))
            captured_payloads.append(payload)
            retained = payload["retained_recent_evidence"]
            assert [item["sequence_index"] for item in retained] == [2, 3]
            assert [item["source_range"] for item in retained] == [
                {"start": 2, "end": 2},
                {"start": 3, "end": 3},
            ]
            assert "complete" in " ".join(
                str(item["content"]).lower() for item in retained
            )
            return retained_artifact

        with patch(
            "core.memory.session_map.service._invoke_session_map_model",
            new=author_without_stale_action,
        ):
            with use_execution_authority(LOCAL_USER_AUTHORITY):
                result = await maybe_auto_compact_after_turn(
                    session_id=session_id,
                    vault_name=vault.name,
                    vault_path=str(vault),
                )

        self.soft_assert_equal(
            result.action if result else None,
            "authored",
            "The retained completion should still use the governed map path",
        )
        self.soft_assert_equal(
            [
                item["source_range"]
                for item in captured_payloads[0]["new_evidence_envelopes"]
            ],
            [{"start": 0, "end": 1}],
            "The evicted group should remain a distinct authoring evidence section",
        )
        checkpoint = store.get_latest_context_checkpoint(session_id, vault.name)
        assert checkpoint is not None
        checkpoint_metadata = json.loads(checkpoint.metadata_json or "{}")
        self.soft_assert_equal(
            checkpoint_metadata.get("consumed_through_sequence_index"),
            1,
            "The eviction boundary should stop before the retained raw suffix",
        )
        self.soft_assert_equal(
            checkpoint_metadata.get("map_observed_through_sequence_index"),
            3,
            "The observed boundary should include retained authoring evidence",
        )
        self.soft_assert_equal(
            load_session_map_checkpoint(checkpoint),
            retained_artifact,
            "The retained completion should replace the stale action with a correctly cited artifact",
        )
        effective = store.get_history(session_id, vault.name) or []
        self.soft_assert_equal(
            len(effective),
            3,
            "The empty map should remain paired with the retained raw user and assistant messages",
        )
        self.soft_assert_equal(
            len(store.get_history(session_id, vault.name, mode="raw") or []),
            4,
            "Retained evidence should not alter canonical raw history",
        )
        tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        self.soft_assert_equal(
            [task.status for task in tasks],
            ["completed"],
            "Retained-evidence authoring should remain inside the normal execution task",
        )
        self.soft_assert_equal(
            tasks[0].metadata.get("recent_evidence_message_count"),
            2,
            "The governed task should expose the bounded retained-evidence count",
        )

        retrieval_session_id = "session-map-retrieved-evidence"
        store.ensure_session(
            retrieval_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            retrieval_session_id,
            vault.name,
            [
                _user("The original pilot ceiling is $120,000."),
                _assistant("Recorded the original ceiling."),
                _user("What was the original ceiling?"),
                ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="session_ops",
                            args={
                                "operation": "get_transcript_window",
                                "sequence_index": 0,
                            },
                            tool_call_id="window-1",
                        )
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            tool_name="session_ops",
                            tool_call_id="window-1",
                            content={
                                "status": "ok",
                                "operation": "get_transcript_window",
                                "session_id": retrieval_session_id,
                                "vault_name": vault.name,
                                "messages": [
                                    {
                                        "sequence_index": 0,
                                        "role": "user",
                                        "content": "The original pilot ceiling is $120,000.",
                                    }
                                ],
                            },
                        )
                    ]
                ),
                _assistant("The original ceiling was $120,000."),
            ],
        )
        retrieval_plan = SteppedHistoryEvictionPlan(
            status="planned",
            reason="above_high_watermark",
            history_revision=1,
            high_watermark_tokens=2,
            low_watermark_tokens=1,
            estimated_tokens_before=10,
            estimated_tokens_after=8,
            message_count_before=6,
            evicted_message_count=2,
            retained_message_count=4,
            group_count=2,
            evicted_group_count=1,
            retained_prefix_count=0,
            eviction_start_index=0,
            eviction_end_index=2,
            minimum_retained_groups=1,
        )
        retained_after_retrieval = _build_retained_session_map_evidence(
            store=store,
            session_id=retrieval_session_id,
            vault_name=vault.name,
            plan=retrieval_plan,
        )
        retrieved_canonical = _build_retrieved_session_map_evidence(
            store=store,
            session_id=retrieval_session_id,
            vault_name=vault.name,
            plan=retrieval_plan,
        )
        self.soft_assert_equal(
            [message.sequence_index for message in retained_after_retrieval],
            [2, 3, 5],
            "The retrieval envelope itself should not become map evidence",
        )
        self.soft_assert_equal(
            [message.sequence_index for message in retrieved_canonical],
            [0],
            "A transcript window should rehydrate its canonical source message",
        )

        self.assert_no_failures()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
