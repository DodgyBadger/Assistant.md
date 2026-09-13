"""Validate that expected Gmail failures settle without aborting chat."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic_ai.messages import (
    ModelMessage,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(
        prefix="assistantmd-gmail-failure-chat-"
    )
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    system_root = direct_root / "system"
    data_root.mkdir()
    system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=system_root)

from core.integrations.google.gmail import GmailError  # noqa: E402
from core.tools.gmail import Gmail  # noqa: E402
from validation.core.base_scenario import BaseScenario  # noqa: E402


class GmailToolFailureChatScenario(BaseScenario):
    """Prove a Gmail 404 becomes a failed tool result and chat continues."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("GmailToolFailureChatVault")
        await self.start_system()
        result: dict[str, object] = {}

        async def stream(
            messages: list[ModelMessage], _info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            tool_returns = [
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if not tool_returns:
                yield {
                    0: DeltaToolCall(
                        name="gmail",
                        json_args=(
                            '{"operation":"get_message",'
                            '"message_id":"stale-draft-message"}'
                        ),
                        tool_call_id="gmail-missing-message",
                    )
                }
                return
            failure = tool_returns[-1]
            self.soft_assert(
                "Do not retry the same resource ID" in failure.model_response_str()
                and "Search Gmail again" in failure.model_response_str(),
                "The model should receive actionable Gmail recovery guidance",
            )
            self.soft_assert_equal(
                failure.metadata.get("failure_kind"),
                "not_found",
                "The model tool return should preserve the Gmail failure kind",
            )
            yield "Recovered after Gmail 404"

        def prepare_agent_config(
            vault_name: str,
            vault_path: str,
            tools: list[str],
            model_name: str,
            thinking: object = None,
            chat_mode: str | None = None,
        ) -> tuple[str, str, FunctionModel, list[object]]:
            del vault_name, tools, model_name, thinking, chat_mode
            return (
                "Call Gmail once, then explain the result.",
                "",
                FunctionModel(stream_function=stream),
                [Gmail.get_tool(vault_path=vault_path)],
            )

        import core.chat.executor as chat_executor

        original_prepare = chat_executor._prepare_agent_config
        chat_executor._prepare_agent_config = prepare_agent_config
        try:
            with patch(
                "core.tools.gmail.get_runtime_context",
                return_value=SimpleNamespace(gmail=_MissingMessageService()),
            ):
                result = await self.run_chat_task(
                    {
                        "vault_name": vault.name,
                        "prompt": "Inspect the draft message.",
                        "session_id": "gmail_tool_failure_chat",
                        "tools": ["gmail"],
                        "model": "test",
                    }
                )
        finally:
            chat_executor._prepare_agent_config = original_prepare
            await self.stop_system()

        self.soft_assert_equal(
            result.get("terminal_event", {}).get("event"),
            "done",
            "A Gmail domain failure should not abort the chat task",
        )
        self.soft_assert(
            "Recovered after Gmail 404" in str(result.get("text")),
            "The agent should continue to a final response after the Gmail failure",
        )
        finished = next(
            (
                event
                for event in result.get("events", [])
                if event.get("event") == "tool_call_finished"
                and event.get("tool_name") == "gmail"
            ),
            None,
        )
        self.soft_assert(finished is not None, "The Gmail tool call should finish")
        if finished is not None:
            self.soft_assert_equal(
                finished.get("terminal_state"),
                "failed",
                "The Gmail tool call should settle in the failed state",
            )
            self.soft_assert(
                "result" not in finished
                and "result_metadata" not in finished
                and "artifact_ref" not in finished,
                "The streamed failure event should omit tool result details",
            )
        self.assert_no_failures()
        self.teardown_scenario()


class _MissingMessageService:
    async def get_message(self, *_args: object, **_kwargs: object) -> None:
        raise GmailError("Gmail resource was not found.", category="not_found")


if __name__ == "__main__":
    asyncio.run(GmailToolFailureChatScenario().test_scenario())
