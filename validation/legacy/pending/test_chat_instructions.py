# Deferred reference only; see validation/registry/pytest-gaps.json.
# Original complete source is available in Git before the 2026-10-31 cleanup.
"""Focused tests for primary chat instruction composition."""

from core.chat.instructions import primary_chat_instruction_layers
from core.constants import DEFERRED_REVIEW_RESUME_INSTRUCTION


def test_primary_chat_instruction_layers_add_deferred_review_resume_notice() -> None:
    ordinary = primary_chat_instruction_layers(
        base_instructions="base",
        tool_instructions="tools",
        has_advanced_shell=False,
    )
    resumed = primary_chat_instruction_layers(
        base_instructions="base",
        tool_instructions="tools",
        has_advanced_shell=False,
        deferred_review_resume=True,
    )

    assert ordinary == ("base", "tools")
    assert resumed == ("base", "tools", DEFERRED_REVIEW_RESUME_INSTRUCTION)
