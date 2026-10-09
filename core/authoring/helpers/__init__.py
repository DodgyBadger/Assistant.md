"""Catalog of Monty helper definitions."""

from __future__ import annotations

from core.authoring.contracts import AuthoringCapabilityDefinition
from core.authoring.helpers.finish import build_definition as build_finish_definition
from core.authoring.helpers.history import (
    build_assemble_context_definition,
    build_retrieve_history_definition,
)
from core.authoring.helpers.parse_markdown import (
    build_definition as build_parse_markdown_definition,
)
from core.authoring.helpers.pending_files import (
    build_definition as build_pending_files_definition,
)
from core.authoring.helpers.read_cache import (
    build_definition as build_read_cache_definition,
)


def get_builtin_helper_definitions() -> tuple[AuthoringCapabilityDefinition, ...]:
    """Return the default helper catalog in a stable registration order."""
    return (
        build_read_cache_definition(),
        build_pending_files_definition(),
        build_retrieve_history_definition(),
        build_assemble_context_definition(),
        build_parse_markdown_definition(),
        build_finish_definition(),
    )
