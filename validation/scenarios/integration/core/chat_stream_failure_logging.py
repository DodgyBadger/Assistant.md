"""
Integration scenario that forces streaming chat execution failure and verifies
the activity log captures generator-path diagnostics.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.chat.executor import PreparedChatExecution
from core.utils.messages import extract_role_and_text
from validation.support.base_scenario import BaseScenario
from validation.support.streaming import stream_events_context


class _FailingStreamAgent:
    """Fake agent that fails after streaming starts."""

    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        raise RuntimeError("forced streaming chat failure for logging validation")
        yield None


class ChatStreamFailureLoggingScenario(BaseScenario):
    """Validate streaming chat failure hardening leaves activity-log evidence."""

    async def test_scenario(self):
        vault = self.create_vault("ChatStreamFailureVault")

        await self.start_system()

        from pydantic_ai.models.test import TestModel

        import core.chat.executor as chat_executor

        async def _prepared_failure(*args, **kwargs):
            return PreparedChatExecution(
                agent=_FailingStreamAgent(),
                message_history=None,
                prompt_for_history="Trigger the forced streaming chat failure.",
                user_prompt="Trigger the forced streaming chat failure.",
                attached_image_count=0,
                model="test",
                tools=[],
            )

        original_prepare_chat_execution = chat_executor._prepare_chat_execution
        original_prepare_agent_config = chat_executor._prepare_agent_config
        chat_executor._prepare_chat_execution = _prepared_failure
        session_id = "chat_stream_failure_session"
        try:
            prompt = "Trigger the forced streaming chat failure."
            failed_result = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": prompt,
                    "session_id": session_id,
                    "tools": [],
                    "model": "test",
                }
            )
            assert (
                failed_result["start_response"].status_code == 200
            ), "Streaming failure chat task should start"
            assert (
                failed_result["terminal_event"].get("event") == "error"
            ), "Streaming failure should emit an error event"

            transcript = (
                vault
                / "AssistantMD"
                / "Chat_Sessions"
                / "chat_stream_failure_session.md"
            )
            assert (
                not transcript.exists()
            ), "Failed streaming chat execution should not write a transcript by default"

            detail = self.call_api(
                f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
            )
            assert (
                detail.status_code == 200
            ), "Failed streaming chat session detail should be available"
            detail_payload = detail.json()
            messages = detail_payload.get("messages", [])
            assert (
                len(messages) == 1
            ), "Failed streaming chat should persist only the accepted user turn"
            assert (
                messages[0].get("role") == "user"
            ), "Failed streaming persisted message should be the user prompt"
            latest_failure = detail_payload.get("latest_failure")
            assert (
                latest_failure is not None
            ), "Failed streaming chat should expose an unfinished-turn marker"
            assert (
                latest_failure.get("status") == "failed"
            ), "Failure marker should be failed"
            assert (
                latest_failure.get("phase") == "agent_stream"
            ), "Failure marker should include phase"
            assert (
                latest_failure.get("streaming") is True
            ), "Failure marker should identify streaming"
            assert (
                latest_failure.get("error_type") == "RuntimeError"
            ), "Failure marker should include the error type"
            assert (
                latest_failure.get("accepted_user_sequence_index") == 0
            ), "Failure marker should point at the accepted user message"

            def _patched_prepare_agent_config(
                vault_name, vault_path, tools, model, thinking=None, chat_mode=None
            ):
                del vault_name, vault_path, tools, model, thinking
                return ("Answer briefly.", "", TestModel(), [])

            captured_preflight_history = []

            async def _capturing_prepare_chat_execution(*args, **kwargs):
                prepared = await original_prepare_chat_execution(*args, **kwargs)
                captured_preflight_history.extend(
                    extract_role_and_text(message)
                    for message in (prepared.message_history or [])
                )
                return prepared

            chat_executor._prepare_agent_config = _patched_prepare_agent_config
            chat_executor._prepare_chat_execution = _capturing_prepare_chat_execution

            follow_up = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Continue after the streaming failure.",
                    "session_id": session_id,
                    "tools": [],
                    "model": "test",
                }
            )
            assert follow_up["text"], "Follow-up chat execution should complete"
            assert (
                follow_up["terminal_event"].get("event") == "done"
            ), "Follow-up chat task should complete"
            assert captured_preflight_history[0] == (
                "user",
                "Trigger the forced streaming chat failure.",
            ), "The next turn should load the failed accepted user prompt"
            assert any(
                role == "system" and "previous user request was accepted" in content
                for role, content in captured_preflight_history
            ), "The next turn should include internal recovery context for the failed response"
            recovered_detail = self.call_api(
                f"/api/chat/sessions/{session_id}?vault_name={vault.name}"
            )
            assert (
                recovered_detail.status_code == 200
            ), "Recovered chat session detail should load"
            assert (
                recovered_detail.json().get("latest_failure") is None
            ), "Successful follow-up should clear the unfinished-turn marker"

            activity_log = self.call_api("/api/system/activity-log")
            assert activity_log.status_code == 200, "Activity log fetch should succeed"
            entries = activity_log.json()["entries"]
            content = json.dumps(entries)

            assert (
                '"message": "Streaming chat execution started"' in content
            ), "Activity log should include streaming start"
            assert (
                '"message": "Streaming chat execution failed"' in content
            ), "Activity log should include structured streaming failure"
            assert (
                '"event": "chat_turn_failed"' in content
            ), "Activity log should include stable streaming failure event"
            assert (
                '"status": "failed"' in content
            ), "Activity log should include normalized streaming failure status"
            assert (
                '"session_id": "chat_stream_failure_session"' in content
            ), "Activity log should include the failing streaming session id"
            assert (
                '"tool_call_count": 0' in content
            ), "Activity log should include compact streaming tool summary"
            failure_entries = [
                entry
                for entry in entries
                if entry.get("message") == "Streaming chat execution failed"
            ]
            assert failure_entries and all(
                (entry.get("data") or {}).get("task_id") for entry in failure_entries
            ), "Activity log should identify the failed streaming task"
            assert (
                '"error_type": "RuntimeError"' in content
            ), "Activity log should include the streaming exception type"
            assert (
                "forced streaming chat failure for logging validation" in content
            ), "Activity log should include the streaming failure message"
            assert (
                '"event": "chat_turn_failure_marker_recorded"' in content
            ), "Activity log should include marker persistence evidence"
        finally:
            chat_executor._prepare_chat_execution = original_prepare_chat_execution
            chat_executor._prepare_agent_config = original_prepare_agent_config
            await self.stop_system()
            self.teardown_scenario()
