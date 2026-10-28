"""
Integration scenario that forces chat execution failure and verifies the
activity log captures session-scoped diagnostics.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from validation.support.base_scenario import BaseScenario


class ChatFailureLoggingScenario(BaseScenario):
    """Validate chat failure hardening leaves durable activity log evidence."""

    async def test_scenario(self):
        vault = self.create_vault("ChatFailureVault")

        await self.start_system()

        import core.chat.executor as chat_executor

        async def _forced_failure(*args, **kwargs):
            raise RuntimeError("forced chat failure for logging validation")

        async def _forced_user_correctable_failure(*args, **kwargs):
            raise ValueError("Image file not found: missing-image.png")

        original_prepare_chat_execution = chat_executor._prepare_chat_execution
        chat_executor._prepare_chat_execution = _forced_failure
        try:
            prompt = "Trigger the forced chat failure."
            chat_result = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": prompt,
                    "session_id": "chat_failure_session",
                    "tools": [],
                    "model": "test",
                },
            )
            assert (
                chat_result["start_response"].status_code == 200
            ), "Forced chat failure task should start"
            assert (
                chat_result["terminal_event"].get("event") == "error"
            ), "Forced chat failure should emit a terminal error"

            transcript = (
                vault / "AssistantMD" / "Chat_Sessions" / "chat_failure_session.md"
            )
            assert (
                not transcript.exists()
            ), "Failed chat execution should not write a transcript by default"

            activity_log = self.call_api("/api/system/activity-log")
            assert activity_log.status_code == 200, "Activity log fetch should succeed"
            content = json.dumps(activity_log.json()["entries"])

            assert (
                '"message": "Chat task request accepted"' in content
            ), "Activity log should include chat request acceptance"
            assert (
                '"message": "Queued streaming chat preflight failed"' in content
            ), "Activity log should include structured chat execution failure"
            assert (
                '"event": "chat_turn_failed"' in content
            ), "Activity log should include stable chat failure event"
            assert (
                '"status": "failed"' in content
            ), "Activity log should include normalized chat failure status"
            assert (
                '"session_id": "chat_failure_session"' in content
            ), "Activity log should include the failing session id"
            assert (
                '"error_type": "RuntimeError"' in content
            ), "Activity log should include the exception type"
            assert (
                "forced chat failure for logging validation" in content
            ), "Activity log should include the failure message"

            chat_executor._prepare_chat_execution = _forced_user_correctable_failure
            image_error_result = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": prompt,
                    "session_id": "chat_image_preflight_failure_session",
                    "tools": [],
                    "model": "test",
                },
            )
            assert (
                image_error_result["terminal_event"].get("event") == "error"
            ), "User-correctable preflight failure should emit a terminal error"
            assert (
                "Image file not found: missing-image.png" in image_error_result["text"]
            ), "User-correctable preflight failure should preserve the actionable message"
        finally:
            chat_executor._prepare_chat_execution = original_prepare_chat_execution
            await self.stop_system()
            self.teardown_scenario()
