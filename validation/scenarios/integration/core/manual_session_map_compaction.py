"""Validate strategy-aware manual Compaction v2 through the public API."""

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

from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.models import (  # noqa: E402
    SessionMapDraft,
    SessionMapEntry,
    SessionMapTrajectory,
    SourceRange,
)
from core.runtime.execution_tasks import ExecutionTaskKind  # noqa: E402
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class ManualSessionMapCompactionScenario(BaseScenario):
    """Keep policy, strategy pinning, focus, and task ownership distinct."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("ManualSessionMapCompactionVault")
        await self.start_system()

        import core.memory.session_map.service as session_map_service

        for key, value in (
            ("compaction_strategy", "session_map"),
            ("compaction_author_model", "test"),
            ("compaction_author_thinking", "low"),
            ("compaction_low_watermark_tokens", "50"),
            ("compaction_retained_turns", "1"),
            ("compaction_high_watermark_tokens", "100000"),
            ("compaction_type", "suggested"),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            assert response.status_code == 200, f"{key} setting should update"

        runtime = get_runtime_context()
        store = runtime.chat_store
        session_id = "manual-session-map-compaction"
        store.ensure_session(
            session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            session_id,
            vault.name,
            [
                _user("Explore whether the redevelopment needs legal review."),
                _assistant("Legal review is one option, not yet a decision."),
                _user("Compare that option with an internal policy review."),
                _assistant("Both routes remain open."),
                _user("The land ownership question is still unresolved."),
                _assistant("I will keep it as an open question."),
                _user("For now, prepare the comparison only."),
                _assistant("I will preserve that current direction."),
            ],
        )

        captured_prompts: list[dict[str, object]] = []

        async def authored_map(**kwargs: object) -> SessionMapDraft:
            payload = json.loads(str(kwargs["prompt"]))
            captured_prompts.append(payload)
            sources = tuple(
                SourceRange(**item["source_range"])
                for item in payload["new_evidence_envelopes"]
            )
            return SessionMapDraft(
                trajectory=SessionMapTrajectory(
                    text="The session is comparing review routes without adopting one.",
                    sources=sources,
                ),
                entries=(
                    SessionMapEntry(
                        id="review_route_comparison",
                        kind="option",
                        state="active",
                        basis="mixed",
                        text="Legal and internal policy review remain alternatives.",
                        sources=sources,
                    ),
                ),
            )

        original_author = session_map_service._invoke_session_map_model
        session_map_service._invoke_session_map_model = authored_map
        try:
            status_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compaction-status?vault_name={vault.name}"
            )
            first_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compact",
                method="POST",
                data={
                    "vault_name": vault.name,
                    "focus": "Emphasize that the review routes remain options.",
                },
            )

            for key, value in (
                ("compaction_strategy", "recovery_card"),
                ("compaction_type", "none"),
            ):
                response = self.call_api(
                    f"/api/system/settings/general/{key}",
                    method="PUT",
                    data={"value": value},
                )
                assert response.status_code == 200, f"{key} setting should update"
            store.add_messages(
                session_id,
                vault.name,
                [
                    _user("Add cost as another comparison dimension."),
                    _assistant("Cost is now part of the comparison."),
                    _user("Do not choose a route yet."),
                    _assistant("Both routes remain options."),
                ],
            )
            second_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compact",
                method="POST",
                data={"vault_name": vault.name},
            )
            unavailable_response = self.call_api(
                f"/api/chat/sessions/{session_id}/compact",
                method="POST",
                data={"vault_name": vault.name},
            )
        finally:
            session_map_service._invoke_session_map_model = original_author

        self.soft_assert_equal(
            status_response.status_code,
            200,
            "Compaction status should remain available under suggested policy",
        )
        status = status_response.json()
        self.soft_assert_equal(
            (
                status["strategy"],
                status["recommended"],
                status["manual_compaction_available"],
            ),
            ("session_map", False, True),
            "Status should distinguish manual availability from the automatic threshold",
        )
        self.soft_assert_equal(
            first_response.status_code,
            200,
            "Suggested policy should permit explicit session-map compaction",
        )
        self.soft_assert_equal(
            second_response.status_code,
            200,
            "None policy should still permit an explicitly approved compaction",
        )
        self.soft_assert_equal(
            (
                unavailable_response.status_code,
                unavailable_response.json().get("status"),
                unavailable_response.json().get("reason"),
            ),
            (200, "unavailable", "retained_turn_floor"),
            "A request with no safely evictable group should return a stable no-op",
        )
        self.soft_assert_equal(
            (
                first_response.json().get("strategy"),
                second_response.json().get("strategy"),
            ),
            ("session_map", "session_map"),
            "The first checkpoint should pin later manual compactions to session_map",
        )
        self.soft_assert_equal(
            captured_prompts[0].get("user_focus"),
            "Emphasize that the review routes remain options.",
            "Manual focus should reach the map author as a separate prompt field",
        )
        self.soft_assert_equal(
            captured_prompts[1].get("user_focus"),
            None,
            "Focus should apply only to the requested rewrite",
        )

        history_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.HISTORY_COMPACTION.value
        )
        author_tasks = await runtime.task_coordinator.list_tasks(
            kind=ExecutionTaskKind.SESSION_MAP_AUTHORING.value
        )
        history_ids = {task.task_id for task in history_tasks}
        self.soft_assert_equal(
            len(history_tasks),
            3,
            "Each API request should own one history-compaction task",
        )
        self.soft_assert(
            len(author_tasks) == 2
            and all(task.parent_task_id in history_ids for task in author_tasks),
            "Each map-authoring task should be nested under its compaction task",
        )
        checkpoints = store.list_context_checkpoints(session_id, vault.name)
        self.soft_assert_equal(
            [checkpoint.checkpoint_kind for checkpoint in checkpoints],
            ["session_map", "session_map"],
            "Manual compaction should create successive inspectable map revisions",
        )
        self.assert_no_failures()


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
