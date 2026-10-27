"""Experiment probe for OAuth-backed OpenAI Codex request-shape assumptions.

This is intentionally kept in experiments because it reaches into Pydantic AI's
OpenAI Responses mapper. The probe gives us a cheap warning if the dependency
changes the specific history replay behavior the Codex endpoint relies on.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import ModelRequestParameters  # noqa: E402
from pydantic_ai.models.openai import (  # noqa: E402
    OpenAIResponsesModel,
    OpenAIResponsesModelSettings,
)
from pydantic_ai.providers.openai import OpenAIProvider  # noqa: E402

from core.llm.model_factory import _apply_openai_oauth_responses_settings  # noqa: E402
from validation.support.base_scenario import BaseScenario  # noqa: E402


class OpenAIOAuthCodexRequestShapeProbeScenario(BaseScenario):
    """Validate the store-disabled Codex replay shape without a network call."""

    async def test_scenario(self):
        oauth_settings_kwargs: dict[str, object] = {}
        _apply_openai_oauth_responses_settings(oauth_settings_kwargs)

        self.soft_assert_equal(
            oauth_settings_kwargs.get("openai_store"),
            False,
            "OAuth-backed OpenAI requests should disable Responses storage",
        )
        self.soft_assert_equal(
            oauth_settings_kwargs.get("openai_send_reasoning_ids"),
            False,
            "OAuth-backed OpenAI requests should not replay store-bound item ids",
        )

        oauth_mapped = await _map_probe_history(
            OpenAIResponsesModelSettings(**oauth_settings_kwargs)
        )
        api_key_mapped = await _map_probe_history(
            OpenAIResponsesModelSettings(openai_send_reasoning_ids=True)
        )

        self.soft_assert(
            not _contains_item_id(oauth_mapped),
            "OAuth-shaped history should omit Responses item ids",
        )
        self.soft_assert(
            _contains_call_id(oauth_mapped, "call_probe"),
            "OAuth-shaped history should preserve function tool call ids",
        )
        self.soft_assert(
            _contains_output_for_call(oauth_mapped, "call_probe"),
            "OAuth-shaped history should preserve tool return call ids",
        )
        self.soft_assert(
            _contains_item_id(api_key_mapped),
            "Control mapping should replay item ids when explicitly enabled",
        )

        (self.artifacts_dir / "oauth_mapped_history.json").write_text(
            json.dumps(oauth_mapped, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.artifacts_dir / "api_key_control_mapped_history.json").write_text(
            json.dumps(api_key_mapped, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        self.teardown_scenario()
        self.assert_no_failures()


async def _map_probe_history(
    settings: OpenAIResponsesModelSettings,
) -> list[dict[str, object]]:
    model = OpenAIResponsesModel(
        "gpt-5.5",
        provider=OpenAIProvider(
            api_key="not-used",
            base_url="http://example.invalid/v1",
        ),
    )
    _, mapped = await model._map_messages(  # noqa: SLF001
        [
            ModelResponse(
                parts=[
                    TextPart(
                        content="Previous assistant text.",
                        id="msg_probe",
                        provider_name="openai",
                    ),
                    ToolCallPart(
                        tool_name="file_read",
                        args={"operation": "read", "path": "notes/probe.md"},
                        tool_call_id="call_probe|fc_probe",
                        id="fc_probe",
                        provider_name="openai",
                    ),
                ],
                provider_name="openai",
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="file_read",
                        content={"ok": True, "content": "probe result"},
                        tool_call_id="call_probe|fc_probe",
                    )
                ],
            ),
        ],
        settings,
        ModelRequestParameters(function_tools=[], native_tools=[], output_tools=[]),
    )
    return [dict(item) for item in mapped]


def _contains_item_id(items: list[dict[str, object]]) -> bool:
    return any("id" in item for item in items)


def _contains_call_id(items: list[dict[str, object]], call_id: str) -> bool:
    return any(
        item.get("type") == "function_call" and item.get("call_id") == call_id
        for item in items
    )


def _contains_output_for_call(items: list[dict[str, object]], call_id: str) -> bool:
    return any(
        item.get("type") == "function_call_output" and item.get("call_id") == call_id
        for item in items
    )


if __name__ == "__main__":
    asyncio.run(OpenAIOAuthCodexRequestShapeProbeScenario().test_scenario())
