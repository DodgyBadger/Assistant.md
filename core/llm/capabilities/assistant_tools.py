"""AssistantMD settings-backed tool capabilities."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import PrepareTools, Toolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FunctionToolset


def build_assistant_tools_capabilities(
    *,
    tools: Sequence[Any],
    instructions: str,
) -> list[Any]:
    """Build Pydantic AI capabilities for AssistantMD tool exposure."""
    if not tools:
        return []

    capabilities: list[Any] = [
        Toolset(
            FunctionToolset(
                tools,
                id="assistantmd-tools",
                instructions=instructions or None,
            )
        ),
        PrepareTools(_prepare_assistantmd_tool_definitions),
    ]
    return capabilities


async def _prepare_assistantmd_tool_definitions(
    ctx: RunContext[Any],
    tool_defs: list[ToolDefinition],
) -> list[ToolDefinition]:
    """Apply common AssistantMD metadata to prepared tool definitions."""
    del ctx
    prepared: list[ToolDefinition] = []
    for tool_def in tool_defs:
        metadata = dict(tool_def.metadata or {})
        metadata.setdefault("assistantmd", {})
        if isinstance(metadata["assistantmd"], dict):
            metadata["assistantmd"] = {
                "source": "settings",
                **metadata["assistantmd"],
            }
        prepared.append(replace(tool_def, metadata=metadata))
    return prepared
