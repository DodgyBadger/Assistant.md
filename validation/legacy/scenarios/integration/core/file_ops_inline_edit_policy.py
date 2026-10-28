"""Validate inline edit approval policy for split file tools."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai import DeferredToolRequests, ToolApproved
from pydantic_ai.messages import ToolCallPart

from api.exceptions import APIException
from api.services.deferred_reviews import (
    _build_deferred_review_approvals,
    _validate_deferred_review_overrides,
)
from core.authoring.helpers.runtime_common import (
    invoke_bound_tool,
    normalize_tool_result,
)
from core.authoring.shared.tool_binding import resolve_tool_binding
from core.chat.chat_store import ChatStore
from core.chat.deferred_reviews import create_deferred_review
from core.chat.executor import INLINE_EDIT_CHAT_MODE, approval_tools_for_chat_mode
from validation.support.base_scenario import BaseScenario, with_local_user_authority


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
            ChatStore().ensure_session(
                "mkdir-review", vault.name, owner_principal_id="local-user"
            )
            original_args = {
                "operation": "mkdir",
                "path": "Notes/Typo",
                "overwrite": False,
            }
            review = create_deferred_review(
                vault_name=vault.name,
                session_id="mkdir-review",
                originating_task_id="mkdir-task",
                requests=DeferredToolRequests(
                    approvals=[
                        ToolCallPart(
                            tool_name="file_write",
                            args=original_args,
                            tool_call_id="mkdir-call",
                        )
                    ]
                ),
                resume_messages=[],
                resume_config={},
            )
            approvals = _build_deferred_review_approvals(
                review=review,
                artifact_ref=review.artifact_ref,
                decisions=[
                    {
                        "tool_call_id": "mkdir-call",
                        "decision": "approve",
                        "override_args": {"path": "Notes/Correct"},
                    }
                ],
            )
            approved = approvals["mkdir-call"]
            assert isinstance(approved, ToolApproved)
            assert approved.override_args is not None
            assert approved.override_args["operation"] == "mkdir"
            assert approved.override_args["overwrite"] is False
            arguments = [approved.override_args] + [
                {**approved.override_args, "path": path}
                for path in ["../mkdir-escape", "Notes/../../mkdir-escape"]
            ]
            for index, args in enumerate(arguments):
                result = await invoke_bound_tool(
                    normal_binding.tool_functions[1],
                    tool_name="file_write",
                    arguments=args,
                    run_buffers={},
                    session_buffers={},
                    session_id="mkdir-review",
                    vault_name=vault.name,
                )
                normalized = normalize_tool_result(
                    "file_write", result, vault_path=str(vault)
                )
                assert normalized.metadata["status"] == (
                    "completed" if index == 0 else "error"
                )
            assert (vault / "Notes/Correct").is_dir()
            assert not (vault / "Notes/Typo").exists()
            assert not (vault.parent / "mkdir-escape").exists()
            for original, override in [
                (original_args, {"operation": "delete"}),
                (original_args, {"overwrite": True}),
                ({"operation": "write", "path": "Fixed.md"}, {"path": "Other.md"}),
            ]:
                try:
                    _validate_deferred_review_overrides(
                        tool_call_id="call",
                        original_args=original,
                        override_args=override,
                        artifact_ref="review",
                    )
                except APIException as error:
                    assert error.status_code == 400
                else:
                    raise AssertionError(
                        "Operation, policy, and non-mkdir targets remain immutable"
                    )
        finally:
            await self.stop_system()
            self.teardown_scenario()
