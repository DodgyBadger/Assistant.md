"""Capability registry for the experimental Monty-backed authoring runtime."""

from __future__ import annotations

import inspect
from collections.abc import Iterable
from typing import Any

from core.authoring.contracts import (
    AuthoringCapabilityCall,
    AuthoringCapabilityDefinition,
    AuthoringCapabilityError,
    AuthoringExecutionContext,
    UnknownAuthoringCapabilityError,
)


class AuthoringCapabilityRegistry:
    """Registry of authoring capabilities available to the Monty runtime."""

    def __init__(
        self,
        definitions: Iterable[AuthoringCapabilityDefinition] | None = None,
    ) -> None:
        self._definitions: dict[str, AuthoringCapabilityDefinition] = {}
        if definitions:
            self.register_many(definitions)

    def register(self, definition: AuthoringCapabilityDefinition) -> None:
        """Register one capability definition."""
        existing = self._definitions.get(definition.name)
        if existing is not None:
            raise AuthoringCapabilityError(
                f"Capability '{definition.name}' is already registered"
            )
        self._definitions[definition.name] = definition

    def register_many(
        self, definitions: Iterable[AuthoringCapabilityDefinition]
    ) -> None:
        """Register multiple capability definitions."""
        for definition in definitions:
            self.register(definition)

    def resolve(self, name: str) -> AuthoringCapabilityDefinition:
        """Resolve one capability definition by name."""
        definition = self._definitions.get(name)
        if definition is None:
            if name == "retrieve_sessions":
                raise UnknownAuthoringCapabilityError(
                    "retrieve_sessions is retired. Use session_ops with list_sessions "
                    "or search_sessions for canonical session discovery."
                )
            raise UnknownAuthoringCapabilityError(f"Unknown capability '{name}'")
        return definition

    def list_names(self) -> tuple[str, ...]:
        """Return sorted registered capability names."""
        return tuple(sorted(self._definitions))

    def build_external_functions(
        self,
        *,
        context: AuthoringExecutionContext,
    ) -> dict[str, Any]:
        """Build the Monty external-functions mapping for one execution."""
        external_functions: dict[str, Any] = {}
        for capability_name in sorted(self._definitions):
            definition = self.resolve(capability_name)

            async def _external_function(
                *args: Any,
                _definition: AuthoringCapabilityDefinition = definition,
                **kwargs: Any,
            ) -> Any:
                call = AuthoringCapabilityCall(
                    capability_name=_definition.name,
                    args=args,
                    kwargs=dict(kwargs),
                )
                result = _definition.handler(call, context)
                if inspect.isawaitable(result):
                    return await result
                return result

            _external_function.__name__ = capability_name
            _external_function.__doc__ = definition.doc
            external_functions[capability_name] = _external_function
        return external_functions
