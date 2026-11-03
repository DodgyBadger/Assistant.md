"""Configured read-only HTTP workload whose failed GET remains replay safe."""

import httpx
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.tools.base import BaseTool, ToolRecoveryPolicy


class ReplaySafeHTTPWorkload(BaseTool):
    @classmethod
    def get_recovery_policy(cls):
        return ToolRecoveryPolicy.REPLAY_SAFE

    @classmethod
    def get_tool(cls, vault_path=None):
        async def replay_safe_http_workload(*, endpoint: str):
            async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                response = await client.get(endpoint)
                response.raise_for_status()
            return ToolReturn(
                return_value=response.text, metadata={"status": "completed"}
            )

        return Tool(
            replay_safe_http_workload,
            name="replay_safe_http_workload",
            description="Read the configured local service without mutating it.",
        )
