"""Chat contracts for deferred, principal-owned MCP tool exposure."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

_direct_run_root: tempfile.TemporaryDirectory[str] | None = None
if __name__ == "__main__":
    from core.runtime.paths import set_bootstrap_roots

    _direct_run_root = tempfile.TemporaryDirectory(prefix="assistantmd-mcp-chat-")
    direct_root = Path(_direct_run_root.name)
    data_root = direct_root / "data"
    system_root = direct_root / "system"
    data_root.mkdir()
    system_root.mkdir()
    set_bootstrap_roots(data_root=data_root, system_root=system_root)

from fastmcp import Client  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402
from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    ToolSearchCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402
from pydantic_ai.usage import UsageLimits  # noqa: E402

from core.identity import ExecutionAuthority  # noqa: E402
from core.llm.capabilities.assistant_tools import (  # noqa: E402
    build_assistant_tools_capabilities,
)
from core.llm.capabilities.chat_tool_output_cache import (  # noqa: E402
    build_chat_tool_output_cache_capability,
)
from core.llm.capabilities.mcp_tools import (  # noqa: E402
    acquire_mcp_chat_capabilities,
    mcp_unavailable_instruction,  # noqa: E402
)
from core.mcp import (  # noqa: E402
    MCPAuthMode,
    MCPConnection,
    MCPConnectionLease,
    MCPReadinessSnapshot,
    MCPTransport,
    MCPUnavailableConnection,
)
from validation.core.base_scenario import BaseScenario  # noqa: E402


class MCPChatToolSearchScenario(BaseScenario):
    """Prove MCP tools stay deferred while using normal chat contracts."""

    async def test_scenario(self) -> None:
        server = FastMCP("assistantmd-chat-probe")

        @server.tool()
        def search_messages(query: str) -> str:
            """Search messages matching a query."""
            return f"matching message for {query}"

        client = Client(server)
        await client.__aenter__()
        released = False

        async def release() -> None:
            nonlocal released
            released = True

        connection = MCPConnection(
            connection_id="gmail-connection",
            slug="gmail",
            display_name="Gmail",
            url="https://gmail.example/mcp",
            transport=MCPTransport.STREAMABLE_HTTP,
            auth_mode=MCPAuthMode.NONE,
            header_name=None,
            enabled=True,
            allow_private_http=False,
            allowed_tools=("search_messages",),
            credential_present=False,
            config_version=1,
            created_at="2026-01-01 00:00:00",
            updated_at="2026-01-01 00:00:00",
        )
        lease = MCPConnectionLease(
            connection=connection,
            client=client,
            tools=tuple(await client.list_tools()),
            release=release,
        )
        manager = _SnapshotManager(MCPReadinessSnapshot((lease,), ()))
        bundle = await acquire_mcp_chat_capabilities(
            manager=manager,
            authority=ExecutionAuthority("chat-owner"),
        )

        def read_note(topic: str) -> str:
            """Read one built-in note."""
            return f"note: {topic}"

        sink = _EventSink()
        capabilities = [
            *build_assistant_tools_capabilities(
                tools=[read_note],
                instructions="",
            ),
            *bundle.capabilities,
            build_chat_tool_output_cache_capability(
                vault_name="TestVault",
                session_id="mcp-chat-session",
                now=None,
                event_sink=sink,
            ),
        ]
        turn = 0

        def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            nonlocal turn
            turn += 1
            definitions = {tool.name: tool for tool in info.function_tools}
            if turn == 1:
                assert not definitions["read_note"].defer_loading
                assert definitions["gmail_search_messages"].defer_loading
                metadata = definitions["gmail_search_messages"].metadata or {}
                assert metadata.get("assistantmd") == {
                    "source": "mcp",
                    "connection_id": "gmail-connection",
                    "connection_name": "Gmail",
                    "connection_slug": "gmail",
                }
                return ModelResponse(
                    parts=[
                        ToolSearchCallPart(
                            args={"queries": ["search Gmail messages"]},
                            tool_call_id="search-mcp",
                        )
                    ]
                )
            if turn == 2:
                assert not definitions["gmail_search_messages"].defer_loading
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="gmail_search_messages",
                            args={"query": "project alpha"},
                            tool_call_id="call-mcp",
                        )
                    ]
                )
            returns = [
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.tool_call_id == "call-mcp"
            ]
            assert len(returns) == 1
            assert "matching message for project alpha" in str(returns[0].content)
            return ModelResponse(parts=[TextPart(content="MCP result observed")])

        try:
            result = await Agent(
                FunctionModel(model),
                capabilities=capabilities,
            ).run(
                "Find a project email",
                usage_limits=UsageLimits(tool_calls_limit=2),
            )
            self.soft_assert_equal(
                result.output,
                "MCP result observed",
                "Chat should execute a discovered prefixed MCP tool",
            )
            self.soft_assert_equal(
                result.usage.tool_calls,
                2,
                "Tool search and MCP execution should consume the normal tool budget",
            )
            self.soft_assert_equal(
                [
                    (event["event_type"], event["tool_name"])
                    for event in sink.events
                    if event["tool_name"] == "gmail_search_messages"
                ],
                [
                    ("call", "gmail_search_messages"),
                    ("result", "gmail_search_messages"),
                ],
                "MCP calls should use the normal chat tool activity hooks",
            )
        finally:
            await bundle.snapshot.close()
            await client.__aexit__(None, None, None)

        self.soft_assert(
            released, "Chat completion should release the MCP catalog lease"
        )
        self.soft_assert_equal(
            mcp_unavailable_instruction(
                (
                    MCPUnavailableConnection(
                        connection_id="offline-id",
                        display_name="Offline Server",
                        status="unreachable",
                        message="The MCP server could not be reached.",
                    ),
                )
            ),
            (
                "MCP availability note: these configured servers were unavailable "
                "during preflight and their tools cannot be used in this run: "
                "Offline Server. Other built-in and MCP tools remain available."
            ),
            "Unavailable servers should produce a sanitized compact instruction",
        )
        self.assert_no_failures()
        self.teardown_scenario()


class _SnapshotManager:
    def __init__(self, snapshot: MCPReadinessSnapshot) -> None:
        self._snapshot = snapshot

    async def acquire_snapshot(
        self,
        authority: ExecutionAuthority,
    ) -> MCPReadinessSnapshot:
        del authority
        return self._snapshot


@dataclass
class _EventSink:
    events: list[dict[str, Any]] = field(default_factory=list)

    def add_tool_event(
        self,
        *,
        session_id: str,
        vault_name: str,
        tool_call_id: str,
        tool_name: str,
        event_type: str,
        args: dict[str, Any] | None = None,
        result_text: str | None = None,
        result_metadata: dict[str, Any] | None = None,
        artifact_ref: str | None = None,
    ) -> None:
        self.events.append(
            {
                "session_id": session_id,
                "vault_name": vault_name,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "event_type": event_type,
                "args": args,
                "result_text": result_text,
                "result_metadata": result_metadata,
                "artifact_ref": artifact_ref,
            }
        )


if __name__ == "__main__":
    asyncio.run(MCPChatToolSearchScenario().test_scenario())
