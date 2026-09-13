"""Probe the Pydantic AI MCP tool-search composition contract."""

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
    ToolSearchCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402

from validation.core.base_scenario import BaseScenario  # noqa: E402


class MCPToolSearchContractProbeScenario(BaseScenario):
    """Protect the deferred MCP discovery and history-replay contract."""

    async def test_scenario(self) -> None:
        gmail_server = FastMCP("gmail-probe")
        marimo_server = FastMCP("marimo-probe")
        calls: list[tuple[str, str]] = []

        @gmail_server.tool()
        def search_email(query: str) -> str:
            """Search Gmail messages for a query."""
            calls.append(("search_email", query))
            return f"email result: {query}"

        @gmail_server.tool()
        def archive_email(message_id: str) -> str:
            """Archive one Gmail message."""
            calls.append(("archive_email", message_id))
            return f"archived: {message_id}"

        @marimo_server.tool()
        def run_notebook(notebook: str) -> str:
            """Run a local Marimo notebook."""
            calls.append(("run_notebook", notebook))
            return f"ran: {notebook}"

        def read_note(topic: str) -> str:
            """Read a built-in note."""
            return f"note: {topic}"

        gmail_tools = (
            MCPToolset(gmail_server, tool_error_behavior="failed")
            .filtered(lambda _ctx, tool: tool.name == "search_email")
            .prefixed("gmail")
            .defer_loading()
            .with_metadata(assistantmd_source="mcp")
        )
        marimo_tools = (
            MCPToolset(marimo_server, tool_error_behavior="failed")
            .prefixed("marimo")
            .defer_loading()
            .with_metadata(assistantmd_source="mcp")
        )
        capabilities = [
            Toolset(gmail_tools),
            Toolset(marimo_tools),
            ToolSearch(strategy="keywords", max_results=2),
        ]

        request_tools: list[set[str]] = []
        deferred_tools: list[set[str]] = []

        def scripted_model(
            _messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            visible = {
                tool.name for tool in info.function_tools if not tool.defer_loading
            }
            deferred_tools.append(
                {tool.name for tool in info.function_tools if tool.defer_loading}
            )
            request_tools.append(visible)
            request_number = len(request_tools)

            if request_number == 1:
                assert visible == {
                    "read_note",
                    "search_tools",
                }, (
                    "Deferred MCP definitions must not enter the initial request; "
                    f"received {sorted(visible)}"
                )
                assert deferred_tools[0] == {
                    "gmail_search_email",
                    "marimo_run_notebook",
                }, "Only allowlisted MCP definitions should enter the search corpus"
                return ModelResponse(
                    parts=[
                        ToolSearchCallPart(
                            args={"queries": ["search Gmail email messages"]},
                            tool_call_id="search-gmail",
                        )
                    ]
                )

            if request_number == 2:
                assert "gmail_search_email" in visible
                assert "gmail_archive_email" not in visible
                assert "marimo_run_notebook" not in visible
                assert "read_note" in visible
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="gmail_search_email",
                            args={"query": "project alpha"},
                            tool_call_id="call-gmail-search",
                        )
                    ]
                )

            return ModelResponse(parts=[TextPart(content="done")])

        agent = Agent(
            FunctionModel(scripted_model),
            tools=[read_note],
            capabilities=capabilities,
        )
        result = await agent.run("Find mail about project alpha")

        assert result.output == "done"
        assert calls == [("search_email", "project alpha")]
        assert len(request_tools) == 3
        assert "gmail_search_email" in request_tools[2]
        assert "gmail_search_email" not in deferred_tools[1]

        replay_tools: list[set[str]] = []

        def replay_model(
            _messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            visible = {
                tool.name for tool in info.function_tools if not tool.defer_loading
            }
            replay_tools.append(visible)
            return ModelResponse(parts=[TextPart(content="replayed")])

        replay_agent = Agent(
            FunctionModel(replay_model),
            tools=[read_note],
            capabilities=capabilities,
        )
        replay_result = await replay_agent.run(
            "Continue with the discovered Gmail tool available",
            message_history=result.all_messages(),
        )

        assert replay_result.output == "replayed"
        assert replay_tools == [
            {"read_note", "search_tools", "gmail_search_email"}
        ], "Persisted search history must restore only the discovered MCP tool"


if __name__ == "__main__":
    asyncio.run(MCPToolSearchContractProbeScenario().test_scenario())
