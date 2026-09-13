"""System-owned instruction composition for primary chat agents."""

from __future__ import annotations

from collections.abc import Callable

from core.constants import ADVANCED_SHELL_FLIGHT_CARD


def primary_chat_instruction_layers(
    *,
    base_instructions: str,
    tool_instructions: str,
    has_advanced_shell: bool,
) -> tuple[str, ...]:
    """Return ordered non-empty instruction layers for one primary chat run."""
    layers = [base_instructions, tool_instructions]
    if has_advanced_shell:
        layers.append(ADVANCED_SHELL_FLIGHT_CARD)
    return tuple(layer for layer in layers if layer)


def constant_instruction(value: str) -> Callable[[], str]:
    """Bind one instruction value without exposing a run-context parameter."""

    def provide() -> str:
        return value

    return provide
