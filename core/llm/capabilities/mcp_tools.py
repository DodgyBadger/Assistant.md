"""Deferred MCP tool-search capabilities for primary chat agents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from mcp.types import Tool as MCPTool
from pydantic_ai import RunContext
from pydantic_ai.capabilities import ToolSearch, Toolset
from pydantic_ai.mcp import CallToolFunc, MCPToolset, ToolResult

from core.identity import ExecutionAuthority
from core.mcp import (
    MCPConnectionManager,
    MCPReadinessSnapshot,
    MCPUnavailableConnection,
)

MCP_TOOL_SEARCH_MAX_RESULTS = 10
MCP_MAX_CONCURRENT_CALLS_PER_CONNECTION = 4


class FrozenCatalogMCPToolset(MCPToolset[Any]):
    """Use one readiness snapshot's definitions for the complete agent run."""

    def __init__(self, *args: Any, frozen_tools: tuple[MCPTool, ...], **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._frozen_tools = frozen_tools

    async def list_tools(self) -> list[MCPTool]:
        """Return frozen definitions instead of relisting a changing server."""
        return list(self._frozen_tools)


@dataclass(frozen=True)
class MCPChatCapabilities:
    """Capabilities and retained leases acquired for one chat execution."""

    capabilities: tuple[Any, ...]
    snapshot: MCPReadinessSnapshot
    unavailable: tuple[MCPUnavailableConnection, ...]
    model_tool_names: tuple[str, ...]

    @property
    def has_tools(self) -> bool:
        return bool(self.model_tool_names)


async def acquire_mcp_chat_capabilities(
    *,
    manager: MCPConnectionManager,
    authority: ExecutionAuthority,
) -> MCPChatCapabilities:
    """Acquire settled MCP catalogs and compose secondary chat capabilities."""
    snapshot = await manager.acquire_snapshot(authority)
    try:
        capabilities: list[Any] = []
        model_tool_names: list[str] = []
        seen_model_tool_names = {"search_tools"}
        for lease in snapshot.leases:
            allowed = (
                set(lease.connection.allowed_tools)
                if lease.connection.allowed_tools is not None
                else None
            )
            frozen_tools = tuple(
                tool for tool in lease.tools if allowed is None or tool.name in allowed
            )
            if not frozen_tools:
                continue
            prefixed_names = tuple(
                f"{lease.connection.slug}_{tool.name}" for tool in frozen_tools
            )
            collisions = seen_model_tool_names.intersection(prefixed_names)
            if collisions:
                raise ValueError("MCP tool names are not unique after prefixing")
            seen_model_tool_names.update(prefixed_names)
            semaphore = asyncio.Semaphore(MCP_MAX_CONCURRENT_CALLS_PER_CONNECTION)

            async def bounded_call(
                ctx: RunContext[Any],
                call_tool: CallToolFunc,
                name: str,
                args: dict[str, Any],
                *,
                _semaphore: asyncio.Semaphore = semaphore,
            ) -> ToolResult:
                del ctx
                async with _semaphore:
                    return await call_tool(name, args)

            toolset = FrozenCatalogMCPToolset(
                lease.client,
                id=f"mcp-{lease.connection.connection_id}",
                frozen_tools=frozen_tools,
                tool_error_behavior="failed",
                process_tool_call=bounded_call,
                cache_tools=True,
                include_instructions=False,
            )
            wrapped = (
                toolset.prefixed(lease.connection.slug)
                .with_metadata(
                    assistantmd={
                        "source": "mcp",
                        "connection_id": lease.connection.connection_id,
                        "connection_name": lease.connection.display_name,
                        "connection_slug": lease.connection.slug,
                    }
                )
                .defer_loading()
            )
            capabilities.append(Toolset(wrapped))
            model_tool_names.extend(prefixed_names)
        if model_tool_names:
            capabilities.append(ToolSearch(max_results=MCP_TOOL_SEARCH_MAX_RESULTS))
        return MCPChatCapabilities(
            capabilities=tuple(capabilities),
            snapshot=snapshot,
            unavailable=snapshot.unavailable,
            model_tool_names=tuple(model_tool_names),
        )
    except BaseException:
        await snapshot.close()
        raise


def mcp_unavailable_instruction(
    unavailable: tuple[MCPUnavailableConnection, ...],
) -> str:
    """Build a compact model-visible note without transport or credential details."""
    names = tuple(item.display_name for item in unavailable)
    if not names:
        return ""
    return (
        "MCP availability note: these configured servers were unavailable during "
        f"preflight and their tools cannot be used in this run: {', '.join(names)}. "
        "Other built-in and MCP tools remain available."
    )
