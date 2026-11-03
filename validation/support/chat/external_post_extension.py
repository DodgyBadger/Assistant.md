"""Configured external commit whose failure cannot establish side-effect absence."""

import httpx
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.tools.base import BaseTool, ToolRecoveryPolicy


class ExternalPostWorkload(BaseTool):
    TOOL_NAME = "external_post_workload"
    POLICY = ToolRecoveryPolicy.MANUAL_REQUIRED

    @classmethod
    def get_recovery_policy(cls):
        return cls.POLICY

    @classmethod
    def get_tool(cls, vault_path=None):
        async def external_post_workload(*, endpoint: str):
            async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                response = await client.post(
                    endpoint, json={"action": "commit external operation"}
                )
                response.raise_for_status()
            return ToolReturn(
                return_value=response.text, metadata={"status": "completed"}
            )

        return Tool(
            external_post_workload,
            name=cls.TOOL_NAME,
            description="Commit an operation to the configured external service.",
        )
