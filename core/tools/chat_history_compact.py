"""Chat-visible tool for current-session history compaction."""

from __future__ import annotations

import json

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from core.chat.compaction import get_compaction_status, run_chat_context_compaction
from core.identity import require_current_execution_authority
from core.logger import UnifiedLogger
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context

from .base import BaseTool, ToolRecoveryPolicy

logger = UnifiedLogger(tag="chat-history-compact-tool")


class ChatHistoryCompact(BaseTool):
    """Check or compact the active chat session history."""

    @classmethod
    def get_tool(cls, vault_path: str | None = None) -> Tool:
        """Get the chat history compaction tool."""

        async def chat_history_compact(
            ctx: RunContext,
            *,
            operation: str = "status",
            focus: str = "",
        ) -> str:
            """Check or compact the current chat session history.

            :param operation: status or compact
            :param focus: Optional user guidance for what the compaction author should emphasize
            """
            deps = getattr(ctx, "deps", None)
            session_id = str(getattr(deps, "session_id", "") or "").strip()
            vault_name = str(getattr(deps, "vault_name", "") or "").strip()
            if not session_id or not vault_name:
                return "chat_history_compact requires active chat session context."

            normalized_operation = (operation or "status").strip().lower()
            if normalized_operation == "status":
                status = await get_compaction_status(
                    session_id=session_id,
                    vault_name=vault_name,
                )
                return json.dumps(status.__dict__, ensure_ascii=False, sort_keys=True)
            if normalized_operation != "compact":
                return "operation must be either 'status' or 'compact'."

            runtime = get_runtime_context()
            effective_vault_path = vault_path or str(
                runtime.config.data_root / vault_name
            )
            logger.info(
                "tool_invoked",
                data={"tool": "chat_history_compact", "operation": "compact"},
            )
            result = await run_chat_context_compaction(
                session_id=session_id,
                vault_name=vault_name,
                vault_path=effective_vault_path,
                focus=focus or None,
                source=ExecutionTaskSource.TOOL,
                authority=require_current_execution_authority(),
                store=runtime.chat_store,
            )
            return json.dumps(result.as_tool_dict(), ensure_ascii=False, sort_keys=True)

        return Tool(
            chat_history_compact,
            name="chat_history_compact",
            description="Check or compact the current chat session history after explicit user approval.",
        )

    @classmethod
    def get_recovery_policy(cls) -> ToolRecoveryPolicy:
        return ToolRecoveryPolicy.MANUAL_REQUIRED
