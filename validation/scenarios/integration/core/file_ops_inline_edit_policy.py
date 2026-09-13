"""Validate inline edit approval policy for split file tools."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.authoring.shared.tool_binding import resolve_tool_binding
from core.chat.executor import INLINE_EDIT_CHAT_MODE, approval_tools_for_chat_mode
from validation.core.base_scenario import BaseScenario, with_local_user_authority


class FileOpsInlineEditPolicyScenario(BaseScenario):
    """Validate inline edit mode gates writes without gating reads."""

    @with_local_user_authority
    async def test_scenario(self) -> None:
        vault = self.create_vault("FileOpsInlineEditPolicyVault")
        await self.start_system()
        try:
            normal_binding = resolve_tool_binding(
                ["file_read", "file_write"], vault_path=str(vault)
            )
            assert normal_binding.tool_names() == ["file_read", "file_write"]
            assert (
                getattr(normal_binding.tool_functions[0], "requires_approval", False)
                is False
            )
            assert (
                getattr(normal_binding.tool_functions[1], "requires_approval", False)
                is False
            )

            inline_edit_binding = resolve_tool_binding(
                ["file_read", "file_write"],
                vault_path=str(vault),
                approval_tool_names=approval_tools_for_chat_mode(INLINE_EDIT_CHAT_MODE),
            )
            assert inline_edit_binding.tool_names() == ["file_read", "file_write"]
            assert (
                getattr(
                    inline_edit_binding.tool_functions[0], "requires_approval", False
                )
                is False
            )
            assert (
                getattr(
                    inline_edit_binding.tool_functions[1], "requires_approval", False
                )
                is True
            )
        finally:
            await self.stop_system()
            self.teardown_scenario()
