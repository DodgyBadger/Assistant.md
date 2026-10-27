"""AssistantMD scenario construction, soft assertions and outcome recording."""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any

from scenario_harness.errors import format_scenario_error
from scenario_harness.lifecycle import ExecutionOutcome, RunContext
from scenario_harness.project import InventoryError, ProjectSpec

from .inventory import describe_scenario


class _Program:
    def __init__(self, context: RunContext, source: Path, instance: Any) -> None:
        self.context = context
        self.source = source
        self.instance = instance

    async def run(self) -> None:
        value = self.instance.test_scenario()
        if inspect.isawaitable(value):
            await value
        self.instance.assert_no_failures()

    def finalize(self, outcome: ExecutionOutcome) -> None:
        details = ""
        if outcome.primary_error is not None:
            details = self.context.redact(
                format_scenario_error(outcome.primary_error, self.source)
            )
        elif outcome.cleanup_errors or outcome.evidence_errors:
            details = "Validation cleanup or evidence finalization failed"
        self.instance.mark_scenario_outcome(
            "passed" if outcome.passed else "failed", details
        )


def create_program(
    context: RunContext, project: ProjectSpec, selector: str, source: Path
) -> _Program:
    definition = describe_scenario(source, project.scenarios_dir)
    module = importlib.import_module(
        f"{project.scenario_module}.{selector.replace('/', '.')}"
    )
    from .base_scenario import BaseScenario

    imported = getattr(module, "__file__", None)
    if imported is None or Path(imported).resolve() != source.resolve():
        raise InventoryError(
            f"Imported scenario source differs from selected source: {source}"
        )
    scenario_class = getattr(module, definition.class_name, None)
    if not inspect.isclass(scenario_class) or not issubclass(
        scenario_class, BaseScenario
    ):
        raise InventoryError(f"Invalid runtime scenario class: {selector}")
    return _Program(context, source, scenario_class())
