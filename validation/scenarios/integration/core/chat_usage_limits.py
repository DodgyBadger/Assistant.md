"""Validate chat model-request and tool-call usage limit handling."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.exceptions import UsageLimitExceeded

from core.chat.executor import (
    PreparedChatExecution,
    _failure_recovery_message,
)
from validation.support.base_scenario import BaseScenario
from validation.support.streaming import stream_events_context


class _RequestLimitAgent:
    """Fake agent that raises Pydantic AI's request-limit exception."""

    async def run(self, *args, **kwargs):
        raise UsageLimitExceeded(
            "The next request would exceed the request_limit of 150"
        )

    @stream_events_context
    async def run_stream_events(self, *args, **kwargs):
        raise UsageLimitExceeded(
            "The next request would exceed the request_limit of 150"
        )
        yield None


class ChatUsageLimitsScenario(BaseScenario):
    """Validate model-request limits are explicit and recoverable in chat paths."""

    async def test_scenario(self):
        vault = self.create_vault("ChatUsageLimitsVault")
        await self.start_system()

        import core.chat.executor as chat_executor

        original_prepare_chat_execution = chat_executor._prepare_chat_execution
        original_chat_usage_limits = chat_executor._chat_usage_limits

        async def _prepared_request_limit(*args, **kwargs):
            return PreparedChatExecution(
                agent=_RequestLimitAgent(),
                message_history=None,
                prompt_for_history="Trigger the request limit.",
                user_prompt="Trigger the request limit.",
                attached_image_count=0,
                model="test",
                tools=[],
            )

        chat_executor._prepare_chat_execution = _prepared_request_limit
        try:
            usage_limits = original_chat_usage_limits()
            self.soft_assert_equal(
                usage_limits.request_limit,
                None,
                "The unlimited model-request default should become Pydantic None",
            )
            self.soft_assert_equal(
                usage_limits.tool_calls_limit,
                None,
                "A disabled chat tool-call setting should become Pydantic None",
            )

            configured_limit = self.call_api(
                "/api/system/settings/general/chat_model_requests_limit",
                method="PUT",
                data={"value": "150"},
            )
            assert configured_limit.status_code == 200, configured_limit.text
            self.soft_assert_equal(
                original_chat_usage_limits().request_limit,
                150,
                "An explicit model-request limit should reach Pydantic",
            )

            request_limit_session = "chat_usage_limit_task"
            request_limit = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Trigger request limit.",
                    "session_id": request_limit_session,
                    "tools": [],
                    "model": "test",
                }
            )
            self.soft_assert_equal(
                request_limit["start_response"].status_code,
                200,
                "Request-limit chat task should start",
            )
            request_limit_error = request_limit["terminal_event"]
            self.soft_assert_equal(
                request_limit_error.get("event") if request_limit_error else None,
                "error",
                "Request-limit chat task should emit an error event",
            )
            self.soft_assert(
                "goal_ops checkpoints" in request_limit["text"],
                "Request-limit message should direct continuation toward durable goal state",
            )
            self.soft_assert_equal(
                (
                    request_limit_error.get("details", {}).get("setting")
                    if request_limit_error
                    else None
                ),
                "chat_model_requests_limit",
                "Request-limit error should identify the controlling setting",
            )
            self.soft_assert_equal(
                (
                    request_limit_error.get("details", {}).get("limit_kind")
                    if request_limit_error
                    else None
                ),
                "request_limit",
                "Request-limit error should identify the Pydantic limit kind",
            )

            detail = self.call_api(
                f"/api/chat/sessions/{request_limit_session}?vault_name={vault.name}"
            )
            self.soft_assert_equal(
                detail.status_code,
                200,
                "Failed request-limit session detail should load",
            )
            latest_failure = detail.json().get("latest_failure")
            self.soft_assert(
                latest_failure is not None,
                "Request-limit failure should persist recovery marker",
            )
            if latest_failure:
                self.soft_assert_equal(
                    latest_failure.get("error_type"),
                    "UsageLimitExceeded",
                    "Failure marker should keep original Pydantic exception type",
                )
                recovery_message = _failure_recovery_message(latest_failure)
                recovery_text = ""
                if recovery_message:
                    recovery_text = " ".join(
                        str(getattr(part, "content", ""))
                        for part in recovery_message.parts
                    )
                self.soft_assert(
                    "goal_ops goals/checkpoints" in recovery_text,
                    "Recovery context should direct the next turn to durable goal checkpoints",
                )
                self.soft_assert(
                    "smaller checkpointed batch" in recovery_text,
                    "Recovery context should ask the next turn to continue in bounded checkpointed work",
                )

            stream_session = "chat_usage_limit_stream"
            stream_result = await self.run_chat_task(
                {
                    "vault_name": vault.name,
                    "prompt": "Trigger streaming request limit.",
                    "session_id": stream_session,
                    "tools": [],
                    "model": "test",
                }
            )
            stream_error = stream_result["terminal_event"]
            self.soft_assert_equal(
                stream_error.get("event") if stream_error else None,
                "error",
                "Streaming request limit should emit an error event",
            )
            if stream_error:
                self.soft_assert(
                    "Model-request limit reached" in stream_result["text"],
                    "Streaming request limit should use model-request wording",
                )
                self.soft_assert(
                    "goal_ops checkpoints" in stream_result["text"],
                    "Streaming request-limit message should direct continuation toward durable goal state",
                )
                self.soft_assert_equal(
                    stream_error.get("details", {}).get("setting"),
                    "chat_model_requests_limit",
                    "Streaming request-limit details should identify the setting",
                )

            stream_detail = self.call_api(
                f"/api/chat/sessions/{stream_session}?vault_name={vault.name}"
            )
            self.soft_assert_equal(
                stream_detail.status_code,
                200,
                "Failed streaming session detail should load",
            )
            self.soft_assert(
                stream_detail.json().get("latest_failure") is not None,
                "Streaming request-limit failure should persist recovery marker",
            )
        finally:
            chat_executor._prepare_chat_execution = original_prepare_chat_execution
            await self.stop_system()
            self.teardown_scenario()

        self.assert_no_failures()
