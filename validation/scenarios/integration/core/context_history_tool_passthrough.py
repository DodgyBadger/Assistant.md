"""
Integration scenario for context-history passthrough during active tool turns.

Validates that the context template processor does not rewrite provider-native
tool-call/tool-return messages once a chat turn is already in the tool loop.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from validation.support.base_scenario import BaseScenario


class ContextHistoryToolPassthroughScenario(BaseScenario):
    """Ensure context processing preserves raw tool turns."""

    async def test_scenario(self):
        vault = self.create_vault("ContextToolPassthroughVault")

        await self.start_system()

        from core.authoring.context_manager import (
            build_context_manager_history_processor,
        )

        session_id = "context_tool_passthrough_session"
        processor = build_context_manager_history_processor(
            session_id=session_id,
            vault_name=vault.name,
            vault_path=str(vault),
            model_alias="gpt",
            template_name="default.md",
        )

        original_messages = [
            ModelRequest(
                parts=[UserPromptPart(content="Review these images.")], run_id="run-1"
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="file_read",
                        args={"operation": "read", "path": "Math/page_images/a.png"},
                        tool_call_id="call-image-1",
                    )
                ],
                run_id="run-1",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="file_read",
                        content="Attached image 'Math/page_images/a.png'.",
                        tool_call_id="call-image-1",
                    )
                ],
                run_id="run-1",
            ),
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="file_read",
                        args={"operation": "read", "path": "Math/page_images/b.png"},
                        tool_call_id="call-image-2",
                    )
                ],
                run_id="run-1",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="file_read",
                        content="Attached image 'Math/page_images/b.png'.",
                        tool_call_id="call-image-2",
                    )
                ],
                run_id="run-1",
            ),
        ]

        checkpoint = self.event_checkpoint()
        processed = await processor(
            SimpleNamespace(prompt="Review these images.", deps=SimpleNamespace()),
            list(original_messages),
        )
        events = self.events_since(checkpoint)

        self.assert_event_contains(
            events,
            name="context_history_passthrough",
            expected={
                "session_id": session_id,
                "vault_name": vault.name,
                "template_name": "default.md",
                "reason": "latest_turn_contains_tool_parts",
            },
        )

        self.soft_assert_equal(
            len(processed),
            len(original_messages),
            "Expected passthrough to preserve message count for active tool turns",
        )
        self.soft_assert(
            [type(message).__name__ for message in processed]
            == [type(message).__name__ for message in original_messages],
            "Expected passthrough to preserve provider-native message types",
        )
        self.soft_assert_equal(
            getattr(processed[1].parts[0], "tool_call_id", None),
            "call-image-1",
            "Expected first tool call id to survive context processing unchanged",
        )
        self.soft_assert_equal(
            getattr(processed[2].parts[0], "tool_call_id", None),
            "call-image-1",
            "Expected first tool return id to survive context processing unchanged",
        )
        self.soft_assert_equal(
            getattr(processed[3].parts[0], "tool_call_id", None),
            "call-image-2",
            "Expected second tool call id to survive context processing unchanged",
        )
        self.soft_assert_equal(
            getattr(processed[4].parts[0], "tool_call_id", None),
            "call-image-2",
            "Expected second tool return id to survive context processing unchanged",
        )
        self.soft_assert_equal(
            getattr(processed[0].parts[0], "content", None),
            "Review these images.",
            "Expected latest user prompt to remain unchanged during passthrough",
        )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
