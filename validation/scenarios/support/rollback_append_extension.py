"""Configured transactional append followed by genuine HTTP status validation."""

from __future__ import annotations

import httpx
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.tools.base import BaseTool, ToolRecoveryPolicy
from core.vault_state.file_operations import append_vault_file_operation


class RollbackAppendWorkload(BaseTool):
    @classmethod
    def get_recovery_policy(cls):
        return ToolRecoveryPolicy.VAULT_TRANSACTIONAL

    @classmethod
    def get_tool(cls, vault_path=None):
        async def rollback_append_workload(*, endpoint: str, path: str, content: str):
            append_vault_file_operation(
                vault_path=vault_path, path=path, content=content
            )
            async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                response = await client.get(endpoint)
                response.raise_for_status()
            return ToolReturn(
                return_value=response.text, metadata={"status": "completed"}
            )

        return Tool(
            rollback_append_workload,
            name="rollback_append_workload",
            description="Append to a vault file, then check the configured local service.",
        )
