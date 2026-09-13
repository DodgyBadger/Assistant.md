"""Validate structured output retries over streamed model transport."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.llm.agents import generate_response
from validation.core.base_scenario import BaseScenario


class _StructuredResult(BaseModel):
    summary: str


class StructuredOutputRetryScenario(BaseScenario):
    """Prove streamed background runs can correct invalid structured output."""

    async def test_scenario(self):
        calls = 0

        async def stream(
            _messages, info: AgentInfo
        ) -> AsyncIterator[dict[int, DeltaToolCall]]:
            nonlocal calls
            calls += 1
            output_tool = info.model_request_parameters.output_tools[0]
            args = "{}" if calls == 1 else '{"summary":"retried output"}'
            yield {
                0: DeltaToolCall(
                    name=output_tool.name,
                    json_args=args,
                    tool_call_id=f"summary-output-{calls}",
                )
            }

        agent = Agent(
            FunctionModel(stream_function=stream),
            output_type=_StructuredResult,
            retries=1,
        )
        result = await generate_response(agent, "Return a structured summary.")

        self.soft_assert_equal(
            result.summary,
            "retried output",
            "Background response collection should return corrected structured output",
        )
        self.soft_assert_equal(
            calls,
            2,
            "Invalid streamed structured output should consume the configured retry",
        )
