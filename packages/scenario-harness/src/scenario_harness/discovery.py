"""Import-free structural discovery of configured scenario entry points."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .project import InventoryError


@dataclass(frozen=True)
class ScenarioDefinition:
    selector: str
    path: str
    class_name: str | None
    kind: str


def collect_scenarios(
    root: Path, *, base_class: str | None = None, entrypoint: str = "test_scenario"
) -> list[ScenarioDefinition]:
    scenarios_root = root / "scenarios"
    if not scenarios_root.is_dir():
        raise InventoryError("Scenario directory is missing")
    definitions = []
    issues = []
    for path in sorted(scenarios_root.rglob("*.py")):
        if "__pycache__" in path.parts or path.name == "__init__.py":
            continue
        try:
            definitions.append(
                describe_scenario(
                    path, scenarios_root, base_class=base_class, entrypoint=entrypoint
                )
            )
        except InventoryError as exc:
            issues.append(str(exc))
    if issues:
        raise InventoryError("\n".join(issues))
    if not definitions:
        raise InventoryError("No scenarios discovered")
    return definitions


def describe_scenario(
    path: Path,
    scenarios_root: Path,
    *,
    base_class: str | None = None,
    entrypoint: str = "test_scenario",
) -> ScenarioDefinition:
    try:
        relative = path.relative_to(scenarios_root)
    except ValueError as exc:
        raise InventoryError("Scenario is outside its suite") from exc
    label = relative.as_posix()
    if path.suffix != ".py" or any(
        part.startswith("_") or not part.isidentifier()
        for part in (*relative.parts[:-1], relative.stem)
    ):
        raise InventoryError(f"{label}: unsupported or hidden scenario module")
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise InventoryError(f"{label}: source/syntax error: {exc}") from exc
    if base_class is None:
        functions = [
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == entrypoint
        ]
        if len(functions) != 1:
            raise InventoryError(
                f"{label}: expected exactly one {entrypoint} entry point"
            )
        return ScenarioDefinition(
            relative.with_suffix("").as_posix(), label, None, "function"
        )
    module, separator, name = base_class.partition(":")
    if not separator:
        module, separator, name = base_class.rpartition(".")
    if not module or not name:
        raise InventoryError("Base class must identify its import module and name")
    base_names = {
        alias.asname or alias.name
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
        if alias.name == name
    }
    classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(
            isinstance(base, ast.Name) and base.id in base_names for base in node.bases
        )
    ]
    if len(classes) != 1:
        raise InventoryError(
            f"{label}: expected exactly one directly imported {name} subclass, found {len(classes)}"
        )
    methods = [
        node
        for node in classes[0].body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name == entrypoint
    ]
    if len(methods) != 1:
        raise InventoryError(f"{label}: expected exactly one {entrypoint} entry point")
    return ScenarioDefinition(
        relative.with_suffix("").as_posix(), label, classes[0].name, "class"
    )
