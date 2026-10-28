"""Production streamed response collection retries invalid real HTTP tool output."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.code_tool_chat import configure_provider
from validation.pytest_scenarios.support.retry_provider import retry_provider

pytestmark = pytest.mark.scenario


class StructuredResult(BaseModel):
    summary: str


async def test_streamed_structured_output_retries_invalid_result(
    app: Application,
) -> None:
    with retry_provider("structured") as provider:
        await app.start()
        configure_provider(app, provider)
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.llm.agents import create_agent, generate_response
        from core.llm.model_factory import build_model_instance

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            agent = await create_agent(
                model=build_model_instance("code-tool-model"),
                output_type=StructuredResult,
                retries=1,
            )
            result = await generate_response(agent, "Return a structured summary.")
        assert result.summary == "retried output"
        assert len(provider.calls) == 2
        assert all(call["stream"] is True for call in provider.calls)
        # The second real HTTP request must contain the validation feedback.
        assert provider.calls[1]["messages"][-1]["role"] == "tool"
        app.evidence.save_json(
            "structured-retry.json",
            {"result": result.model_dump(), "provider_requests": provider.calls},
        )
