"""Stable web search tool with settings-selected strategy."""

from __future__ import annotations

import time

from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.tools.base import BaseTool, ToolRecoveryPolicy
from core.tools.web_common import log_web_capability_completed, web_tool_failure
from core.web.config import get_web_strategy_name
from core.web.service import WebCapabilityService


class WebSearch(BaseTool):
    """Provider-neutral web search tool."""

    @classmethod
    def get_tool(
        cls,
        vault_path: str | None = None,
        *,
        service: WebCapabilityService | None = None,
    ) -> Tool:
        del vault_path
        service = service or WebCapabilityService()

        async def web_search(*, query: str, max_results: int = 3) -> str | ToolReturn:
            """Search the web using the configured strategy.

            :param query: Search query to look up
            :param max_results: Maximum results to return, from 1 to 10
            """
            strategy = get_web_strategy_name("web_search")
            started_at = time.monotonic()
            try:
                result = await service.search(
                    query=query,
                    max_results=max_results,
                    strategy=strategy,
                )
            except Exception as exc:  # noqa: BLE001 - tool boundary
                return web_tool_failure(
                    tool_name="web_search",
                    strategy=strategy,
                    exc=exc,
                    phase="web_search",
                    redactions=[query],
                )
            log_web_capability_completed(
                tool_name="web_search",
                strategy=result.strategy,
                result_count=len(result.items),
                duration_seconds=time.monotonic() - started_at,
            )
            if not result.items:
                return f"No search results found using strategy '{result.strategy}'."
            formatted = [
                f"**{item.title}**\n{item.snippet}\nURL: {item.url}"
                for item in result.items
            ]
            return f"Search results for '{query}':\n\n" + "\n\n---\n\n".join(formatted)

        return Tool(
            web_search,
            name="web_search",
            description=(
                "Search the web using the configured search strategy. Use web_extract "
                "after search when you need the full content of a known page."
            ),
        )

    @classmethod
    def get_recovery_policy(cls) -> ToolRecoveryPolicy:
        return ToolRecoveryPolicy.REPLAY_SAFE
