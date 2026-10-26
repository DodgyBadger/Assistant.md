"""Expose only registered authoring values through Monty's explicit object policy."""

from collections.abc import Callable
from functools import wraps
from inspect import isawaitable
from typing import Any

from pydantic_monty import ClassInstance


class _AuthoringInstance(ClassInstance):
    def __init__(self, value: Any, convert: Callable[[Any], Any]) -> None:
        self._convert = convert
        super().__init__(value, eager_attrs="all", allowed_methods="all")

    def convert_value(self, name: str, value: Any) -> Any:
        return self._convert(value)


class MontyValueConverter:
    """Retain the host's explicit type allowlist across inputs and helper results."""

    def __init__(self, registered_types: tuple[type, ...]) -> None:
        self._registered_types = frozenset(registered_types)

    def convert(self, value: Any) -> Any:
        if type(value) in self._registered_types:
            return _AuthoringInstance(value, self.convert)
        if isinstance(value, dict):
            return {key: self.convert(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.convert(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.convert(item) for item in value)
        return value

    def wrap_function(self, function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        async def call(*args: Any, **kwargs: Any) -> Any:
            result = function(*args, **kwargs)
            if isawaitable(result):
                result = await result
            return self.convert(result)

        return call
