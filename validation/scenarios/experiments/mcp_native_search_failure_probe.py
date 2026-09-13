"""Probe native MCP tool search and model-visible remote failures."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-mcp-probe-")
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    system_root = direct_root / "system"
    data_root.mkdir()
    system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=system_root)

from mcp.server.fastmcp import FastMCP  # noqa: E402
from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.capabilities import ToolSearch, Toolset  # noqa: E402
from pydantic_ai.mcp import MCPToolset  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from validation.core.base_scenario import BaseScenario  # noqa: E402


class MCPNativeSearchFailureProbeScenario(BaseScenario):
    """Protect native deferred definitions and failed-tool semantics."""

    async def test_scenario(self) -> None:
        await _assert_native_search_shape()
        await _assert_model_visible_failure()


async def _assert_native_search_shape() -> None:
    gmail_server = FastMCP("gmail-native-probe")
    marimo_server = FastMCP("marimo-native-probe")

    @gmail_server.tool()
    def search_email(query: str) -> str:
        """Search Gmail messages for a query."""
        return f"email result: {query}"

    @marimo_server.tool()
    def run_notebook(notebook: str) -> str:
        """Run a local Marimo notebook."""
        return f"ran: {notebook}"

    def read_note(topic: str) -> str:
        """Read a built-in note."""
        return f"note: {topic}"

    capabilities = [
        Toolset(
            MCPToolset(gmail_server, tool_error_behavior="failed")
            .prefixed("gmail")
            .defer_loading()
        ),
        Toolset(
            MCPToolset(marimo_server, tool_error_behavior="failed")
            .prefixed("marimo")
            .defer_loading()
        ),
        ToolSearch(max_results=1),
    ]
    request_number = 0

    def native_model(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal request_number
        request_number += 1
        definitions = {tool.name: tool for tool in info.function_tools}

        if request_number == 1:
            assert set(definitions) == {
                "read_note",
                "gmail_search_email",
                "marimo_run_notebook",
            }
            assert not definitions["read_note"].defer_loading
            assert definitions["gmail_search_email"].defer_loading
            assert definitions["marimo_run_notebook"].defer_loading
            assert "search_tools" not in definitions, (
                "Native search should send deferred definitions instead of a "
                "local search_tools function"
            )
            return ModelResponse(
                parts=[
                    ToolSearchCallPart(
                        args={"queries": ["search Gmail email messages"]},
                        tool_call_id="native-search-gmail",
                    )
                ]
            )

        assert not definitions["gmail_search_email"].defer_loading
        assert definitions["marimo_run_notebook"].defer_loading
        assert not definitions["read_note"].defer_loading
        return ModelResponse(parts=[TextPart(content="native search complete")])

    agent = Agent(
        FunctionModel(native_model),
        tools=[read_note],
        capabilities=capabilities,
    )
    result = await agent.run("Find relevant email")

    assert result.output == "native search complete"
    assert request_number == 2


async def _assert_model_visible_failure() -> None:
    failure_server = FastMCP("failure-probe")
    executions = 0

    @failure_server.tool()
    def fail_once(value: str) -> str:
        """Fail deterministically for the MCP error-contract probe."""
        nonlocal executions
        executions += 1
        raise ValueError(f"probe failure for {value}")

    request_number = 0

    def failure_model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        nonlocal request_number
        request_number += 1
        if request_number == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="fail_once",
                        args={"value": "project alpha"},
                        tool_call_id="failed-mcp-call",
                    )
                ]
            )

        failed_returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.outcome == "failed"
        ]
        assert len(failed_returns) == 1
        failed_return = failed_returns[0]
        assert failed_return.tool_name == "fail_once"
        assert failed_return.tool_call_id == "failed-mcp-call"
        assert "probe failure for project alpha" in str(failed_return.content)
        return ModelResponse(parts=[TextPart(content="failure observed")])

    agent = Agent(
        FunctionModel(failure_model),
        toolsets=[
            MCPToolset(failure_server, tool_error_behavior="failed", max_retries=2)
        ],
    )
    result = await agent.run("Call the failing MCP tool")

    assert result.output == "failure observed"
    assert executions == 1, "A server-declared failure must not be retried implicitly"
    assert request_number == 2


if __name__ == "__main__":
    asyncio.run(MCPNativeSearchFailureProbeScenario().test_scenario())
