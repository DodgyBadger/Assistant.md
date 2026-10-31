# Deferred reference only; see validation/registry/pytest-gaps.json.
# Original complete source is available in Git before the 2026-10-31 cleanup.
"""Deterministic tests for advanced-shell infrastructure configuration."""

from pathlib import Path

from core.chat.instructions import primary_chat_instruction_layers
from core.constants import ADVANCED_SHELL_FLIGHT_CARD
from core.runtime.paths import set_bootstrap_roots

_TEST_ROOT = Path("/tmp/assistantmd-advanced-shell-tests")
set_bootstrap_roots(_TEST_ROOT / "data", _TEST_ROOT / "system")


def test_primary_chat_instruction_layers_gate_advanced_shell_exactly_once() -> None:
    restricted = primary_chat_instruction_layers(
        base_instructions="base",
        tool_instructions="tools",
        has_advanced_shell=False,
    )
    advanced = primary_chat_instruction_layers(
        base_instructions="base",
        tool_instructions="tools",
        has_advanced_shell=True,
    )

    assert restricted == ("base", "tools")
    assert advanced == ("base", "tools", ADVANCED_SHELL_FLIGHT_CARD)
    assert advanced.count(ADVANCED_SHELL_FLIGHT_CARD) == 1
