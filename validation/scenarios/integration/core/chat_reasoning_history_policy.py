"""Integration scenario for chat reasoning-part persistence policy."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    UserPromptPart,
)

from core.chat.chat_store import ChatStore
from core.identity import LOCAL_USER_PRINCIPAL_ID
from validation.core.base_scenario import BaseScenario


class ChatReasoningHistoryPolicyScenario(BaseScenario):
    """Validate reasoning parts are transient by default and opt-in persistable."""

    async def test_scenario(self):
        vault = self.create_vault("ChatReasoningHistoryPolicyVault")
        await self.start_system()

        store = ChatStore(system_root=str(self._get_system_controller()._system_root))

        default_session_id = "reasoning-default"
        store.ensure_session(
            default_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            default_session_id,
            vault.name,
            [self._response_with_reasoning()],
        )

        default_history = store.get_history(default_session_id, vault.name) or []
        self.soft_assert_equal(
            self._thinking_part_count(default_history),
            0,
            "Default chat persistence should drop reasoning parts",
        )
        self.soft_assert(
            self._has_visible_text(default_history),
            "Default chat persistence should preserve visible assistant text",
        )
        self.soft_assert(
            self._has_tool_call(default_history),
            "Default chat persistence should preserve tool call parts",
        )
        self.soft_assert_equal(
            self._provider_item_ids(default_history),
            [],
            "Default chat persistence should strip provider item ids when reasoning is dropped",
        )
        default_detail = self.call_api(
            f"/api/chat/sessions/{default_session_id}?vault_name={vault.name}"
        )
        self.soft_assert_equal(
            default_detail.status_code,
            200,
            "Default reasoning-policy session detail should load",
        )
        self.soft_assert(
            "private reasoning summary" not in default_detail.text,
            "Default session detail should not show dropped reasoning parts",
        )
        default_payload = default_detail.json()
        self.soft_assert(
            all(
                not message.get("thinking_content")
                for message in default_payload.get("messages", [])
            ),
            "Default session detail should not expose dropped reasoning content",
        )

        update_setting = self.call_api(
            "/api/system/settings/general/persist_model_reasoning_parts",
            method="PUT",
            data={"value": "true"},
        )
        self.soft_assert_equal(
            update_setting.status_code,
            200,
            "Reasoning persistence setting should be editable",
        )

        opt_in_session_id = "reasoning-opt-in"
        store.ensure_session(
            opt_in_session_id,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(
            opt_in_session_id,
            vault.name,
            [self._response_with_reasoning()],
        )

        opt_in_history = store.get_history(opt_in_session_id, vault.name) or []
        self.soft_assert_equal(
            self._thinking_part_count(opt_in_history),
            1,
            "Opt-in chat persistence should preserve reasoning parts",
        )
        self.soft_assert(
            self._has_visible_text(opt_in_history),
            "Opt-in chat persistence should preserve visible assistant text",
        )
        self.soft_assert(
            self._has_tool_call(opt_in_history),
            "Opt-in chat persistence should preserve tool call parts",
        )
        self.soft_assert_equal(
            self._provider_item_ids(opt_in_history),
            ["reasoning_content", "visible_message", "probe-item"],
            "Opt-in chat persistence should preserve provider item ids with reasoning parts",
        )
        opt_in_detail = self.call_api(
            f"/api/chat/sessions/{opt_in_session_id}?vault_name={vault.name}"
        )
        self.soft_assert_equal(
            opt_in_detail.status_code,
            200,
            "Opt-in reasoning-policy session detail should load",
        )
        opt_in_payload = opt_in_detail.json()
        opt_in_message = next(
            (
                message
                for message in opt_in_payload.get("messages", [])
                if message.get("role") == "assistant"
            ),
            {},
        )
        self.soft_assert_equal(
            opt_in_message.get("thinking_content"),
            "private reasoning summary",
            "Opt-in session detail should expose persisted reasoning separately",
        )
        self.soft_assert_equal(
            opt_in_message.get("content"),
            "Visible answer.",
            "Opt-in session detail should keep visible text separate from reasoning",
        )

        self._test_checkpoint_policy_transitions(store, vault.name)

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()

    def _test_checkpoint_policy_transitions(
        self, store: ChatStore, vault_name: str
    ) -> None:
        for initial_policy, checkpoint_policy in ((True, False), (False, True)):
            session_id = f"reasoning-checkpoint-{initial_policy}-{checkpoint_policy}"
            for policy in (initial_policy, checkpoint_policy):
                response = self.call_api(
                    "/api/system/settings/general/persist_model_reasoning_parts",
                    method="PUT",
                    data={"value": str(policy).lower()},
                )
                assert response.status_code == 200
                if policy == initial_policy:
                    store.ensure_session(
                        session_id,
                        vault_name,
                        owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
                    )
                    store.add_messages(
                        session_id,
                        vault_name,
                        [
                            ModelRequest(parts=[UserPromptPart(content="Question")]),
                            ModelResponse(
                                parts=[
                                    ThinkingPart(
                                        content="Canonical reasoning",
                                        id="canonical-reasoning",
                                    ),
                                    TextPart(
                                        content="Canonical answer",
                                        id="canonical-answer",
                                    ),
                                ]
                            ),
                        ],
                    )
            canonical = store.get_stored_messages(session_id, vault_name, mode="raw")
            canonical_messages = [item.message for item in canonical]
            synthetic = ModelResponse(
                parts=[
                    ThinkingPart(
                        content="Synthetic reasoning", id="synthetic-reasoning"
                    ),
                    TextPart(content="Synthetic answer", id="synthetic-answer"),
                ]
            )
            store.add_compaction_checkpoint(
                session_id=session_id,
                vault_name=vault_name,
                checkpoint_id=f"{session_id}-checkpoint",
                source="validation",
                message_count_before=2,
                last_message_sequence_index=1,
                summary_message=synthetic,
                replacement_history=[synthetic, *canonical_messages],
                replacement_source_sequence_indexes=[None, 0, 1],
            )
            effective = store.get_stored_messages(session_id, vault_name)
            self.soft_assert_equal(
                [item.message for item in effective[1:]],
                canonical_messages,
                "Checkpoint retention must preserve canonical identity across reasoning-policy changes",
            )
            self.soft_assert_equal(
                self._thinking_part_count([effective[0].message]),
                int(checkpoint_policy),
                "Synthetic checkpoint messages must follow the current reasoning-persistence policy",
            )
            replay = store.get_history(session_id, vault_name) or []
            self.soft_assert_equal(
                self._thinking_part_count(replay),
                int(checkpoint_policy) * (1 + int(initial_policy)),
                "Model replay must apply the current policy to synthetic and retained messages",
            )
            child_id = f"{session_id}-child"
            store.fork_session(
                source_session_id=session_id,
                new_session_id=child_id,
                vault_name=vault_name,
                through_sequence_index=1,
                title="Reasoning policy fork",
            )
            self.soft_assert_equal(
                [
                    item.message
                    for item in store.get_stored_messages(child_id, vault_name)[1:]
                ],
                canonical_messages,
                "Checkpoint inheritance must preserve canonical identity across reasoning-policy changes",
            )

    def _response_with_reasoning(self) -> ModelResponse:
        return ModelResponse(
            parts=[
                ThinkingPart(
                    content="private reasoning summary",
                    id="reasoning_content",
                    provider_name="openai",
                ),
                TextPart(
                    content="Visible answer.",
                    id="visible_message",
                    provider_name="openai",
                ),
                ToolCallPart(
                    tool_name="probe",
                    args={"value": 1},
                    tool_call_id="probe-call",
                    id="probe-item",
                    provider_name="openai",
                ),
            ],
            provider_name="openai",
        )

    def _thinking_part_count(self, history) -> int:
        return sum(
            1
            for message in history
            for part in getattr(message, "parts", [])
            if isinstance(part, ThinkingPart)
        )

    def _has_visible_text(self, history) -> bool:
        return any(
            isinstance(part, TextPart) and part.content == "Visible answer."
            for message in history
            for part in getattr(message, "parts", [])
        )

    def _has_tool_call(self, history) -> bool:
        return any(
            isinstance(part, ToolCallPart) and part.tool_call_id == "probe-call"
            for message in history
            for part in getattr(message, "parts", [])
        )

    def _provider_item_ids(self, history) -> list[str]:
        ids: list[str] = []
        for message in history:
            for part in getattr(message, "parts", []):
                item_id = getattr(part, "id", None)
                if item_id:
                    ids.append(item_id)
        return ids
