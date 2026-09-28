"""Validate fail-closed settings and eligibility for stepped session maps."""

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

from core.chat.compaction import build_compaction_summary_message  # noqa: E402
from core.identity import LOCAL_USER_PRINCIPAL_ID  # noqa: E402
from core.memory.session_map.readiness import (  # noqa: E402
    evaluate_session_map_readiness,
)
from core.runtime.state import get_runtime_context  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class SessionMapReadinessScenario(BaseScenario):
    """Keep the experimental strategy disabled unless every dependency is ready."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("SessionMapReadinessVault")
        await self.start_system()
        store = get_runtime_context().chat_store
        canonical_session = "session-map-ready-canonical"
        store.ensure_session(
            canonical_session,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        messages = [_user("Question"), _assistant("Answer")]
        store.add_messages(canonical_session, vault.name, messages)

        settings_response = self.call_api("/api/system/settings/general")
        self.soft_assert_equal(
            settings_response.status_code,
            200,
            "Session-map settings should be exposed through general settings",
        )
        settings = {item["key"]: item for item in settings_response.json()}
        self.soft_assert_equal(
            settings["context_reduction_strategy"]["value"],
            "recovery_card",
            "Recovery-card compaction should remain the default strategy",
        )
        self.soft_assert_equal(
            settings["session_map_author_model"]["value"],
            "none",
            "The session-map-specific author override should default to none",
        )
        self.soft_assert(
            "uses default_model"
            in (settings["session_map_author_model"].get("description") or ""),
            "The author-model setting should explain its default-model fallback",
        )
        initial = evaluate_session_map_readiness(
            store=store,
            session_id=canonical_session,
            vault_name=vault.name,
        )
        self.soft_assert_equal(
            (initial.enabled, initial.reason),
            (False, "strategy_not_enabled"),
            "The default configuration should fail closed",
        )

        for key, value in (
            ("context_reduction_strategy", "stepped_session_map"),
            ("default_model", "test"),
            ("session_map_author_thinking", "low"),
            ("session_map_low_watermark_tokens", "50"),
            ("compaction_token_threshold", "100"),
            ("compaction_type", "auto"),
        ):
            response = self.call_api(
                f"/api/system/settings/general/{key}",
                method="PUT",
                data={"value": value},
            )
            self.soft_assert_equal(
                response.status_code,
                200,
                f"{key} should save through the normal settings API",
            )

        ready = evaluate_session_map_readiness(
            store=store,
            session_id=canonical_session,
            vault_name=vault.name,
        )
        self.soft_assert_equal(
            (
                ready.enabled,
                ready.reason,
                ready.author_model,
                ready.author_thinking,
                ready.high_watermark_tokens,
                ready.low_watermark_tokens,
            ),
            (True, "ready_canonical_history", "test", "low", 100, 50),
            "The default model should make canonical history eligible when no author override is set",
        )

        self._set_setting("session_map_author_model", "jev")
        decision_only = evaluate_session_map_readiness(
            store=store,
            session_id=canonical_session,
            vault_name=vault.name,
        )
        self.soft_assert_equal(
            (decision_only.enabled, decision_only.reason),
            (False, "author_model_not_text_capable"),
            "A decision-only model must not be used as the generative author",
        )

        self._set_setting("session_map_author_model", "test")
        self._set_setting("session_map_author_thinking", "impossible")
        invalid_thinking = evaluate_session_map_readiness(
            store=store,
            session_id=canonical_session,
            vault_name=vault.name,
        )
        self.soft_assert_equal(
            (invalid_thinking.enabled, invalid_thinking.reason),
            (False, "author_thinking_invalid"),
            "An invalid author thinking policy should fail closed",
        )

        self._set_setting("session_map_author_thinking", "low")
        self._set_setting("session_map_low_watermark_tokens", "100")
        invalid_watermarks = evaluate_session_map_readiness(
            store=store,
            session_id=canonical_session,
            vault_name=vault.name,
        )
        self.soft_assert_equal(
            (invalid_watermarks.enabled, invalid_watermarks.reason),
            (False, "invalid_watermarks"),
            "The low watermark must remain below the shared high watermark",
        )

        self._set_setting("session_map_low_watermark_tokens", "50")
        self._set_setting("compaction_type", "suggested")
        manual_only = evaluate_session_map_readiness(
            store=store,
            session_id=canonical_session,
            vault_name=vault.name,
        )
        self.soft_assert_equal(
            (manual_only.enabled, manual_only.reason),
            (False, "automatic_context_reduction_disabled"),
            "Stepped maps should not silently activate under a manual-only policy",
        )

        self._set_setting("compaction_type", "auto")
        compacted_session = "session-map-ready-recovery-card"
        store.ensure_session(
            compacted_session,
            vault.name,
            owner_principal_id=LOCAL_USER_PRINCIPAL_ID,
        )
        store.add_messages(compacted_session, vault.name, messages)
        summary = build_compaction_summary_message("Existing recovery state.")
        store.add_compaction_checkpoint(
            session_id=compacted_session,
            vault_name=vault.name,
            checkpoint_id="existing-recovery-card",
            source="validation",
            message_count_before=2,
            last_message_sequence_index=1,
            summary_message=summary,
            replacement_history=[summary],
        )
        recovery_card = evaluate_session_map_readiness(
            store=store,
            session_id=compacted_session,
            vault_name=vault.name,
        )
        self.soft_assert_equal(
            (
                recovery_card.enabled,
                recovery_card.reason,
                recovery_card.strategy,
            ),
            (False, "recovery_card_checkpoint_present", "recovery_card"),
            "An existing recovery card should pin the session to its current strategy",
        )

        self.assert_no_failures()

    def _set_setting(self, key: str, value: str) -> None:
        response = self.call_api(
            f"/api/system/settings/general/{key}",
            method="PUT",
            data={"value": value},
        )
        if response.status_code != 200:
            raise AssertionError(f"Failed to save {key}: {response.text}")


def _user(content: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=content)])


def _assistant(content: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=content)])
