"""Default scenario program loader for independent projects."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Protocol

from .discovery import InventoryError, describe_scenario
from .lifecycle import ExecutionOutcome, RunContext
from .project import ProjectSpec, resolve_reference


class ScenarioProgram(Protocol):
    def run(self) -> Awaitable[None] | None: ...

    def finalize(self, outcome: ExecutionOutcome) -> Awaitable[None] | None: ...


class _Program:
    def __init__(self, callback: Any) -> None:
        self.callback = callback

    def run(self) -> Any:
        return self.callback()

    def finalize(self, outcome: ExecutionOutcome) -> None:
        pass


def create_program(
    context: RunContext, project: ProjectSpec, selector: str, source: Path
) -> _Program:
    """Load the selected module after setup and verify its source identity."""
    definition = describe_scenario(
        source,
        project.scenarios_dir,
        base_class=project.base_class,
        entrypoint=project.entrypoint,
    )
    module = importlib.import_module(
        f"{project.scenario_module}.{selector.replace('/', '.')}"
    )
    imported_source = getattr(module, "__file__", None)
    if imported_source is None or Path(imported_source).resolve() != source.resolve():
        raise InventoryError("Imported scenario source differs from selected source")
    if definition.kind == "function":
        callback = getattr(module, project.entrypoint, None)
    else:
        if definition.class_name is None:
            raise InventoryError("Missing scenario class name")
        scenario_class = getattr(module, definition.class_name, None)
        if not inspect.isclass(scenario_class):
            raise InventoryError("Invalid runtime scenario class")
        if project.base_class:
            reference = project.base_class
            if ":" not in reference:
                module_name, _, class_name = reference.rpartition(".")
                reference = f"{module_name}:{class_name}"
            base = resolve_reference(reference, project.root)
            if not issubclass(scenario_class, base):
                raise InventoryError("Invalid runtime scenario base class")
        callback = getattr(scenario_class(), project.entrypoint, None)
    if not callable(callback):
        raise InventoryError("Invalid runtime scenario entry point")
    return _Program(callback)
