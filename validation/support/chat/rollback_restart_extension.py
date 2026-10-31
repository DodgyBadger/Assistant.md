"""Configured transactional tool workload with a real post-mutation HTTP failure."""

from __future__ import annotations

import httpx
from pydantic_ai.messages import ToolReturn
from pydantic_ai.tools import Tool

from core.tools.base import BaseTool, ToolRecoveryPolicy
from core.vault_state.file_operations import write_vault_file_operation


class RollbackRestartWorkload(BaseTool):
    @classmethod
    def get_recovery_policy(cls):
        return ToolRecoveryPolicy.VAULT_TRANSACTIONAL

    @classmethod
    def get_tool(cls, vault_path=None):
        async def rollback_restart_workload(*, endpoint: str, path: str, content: str):
            mutation = write_vault_file_operation(
                vault_path=vault_path, path=path, content=content, overwrite=True
            )
            async with httpx.AsyncClient(trust_env=False, timeout=3) as client:
                response = await client.get(endpoint)
                response.raise_for_status()
            return ToolReturn(
                return_value=response.text,
                metadata={"status": "completed", "mutation": mutation.metadata},
            )

        return Tool(
            rollback_restart_workload,
            name="rollback_restart_workload",
            description="Write a vault file transactionally, then verify the configured local service.",
        )
