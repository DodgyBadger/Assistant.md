"""Explicit consumer configuration for discovery and isolated execution."""

from __future__ import annotations

import importlib
import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any


class InventoryError(ValueError):
    """Collection cannot safely identify the complete requested suite."""


def resolve_reference(reference: str, root: Path) -> Any:
    module, separator, name = reference.partition(":")
    if not separator or not module or not name or "." in name:
        raise InventoryError(f"Invalid provider reference: {reference}")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return getattr(importlib.import_module(module), name)


class NullAdapter:
    async def setup(self, context: Any) -> None:
        pass

    async def teardown(self, context: Any) -> None:
        pass


@dataclass(frozen=True)
class ProjectSpec:
    root: Path
    suite_root: Path
    config_path: Path | None = None
    name: str = "Scenarios"
    scenario_module: str = "scenarios"
    base_class: str | None = None
    entrypoint: str = "test_scenario"
    adapter: str = "scenario_harness.project:NullAdapter"
    scenario_loader: str = "scenario_harness.execution:create_program"
    discovery_hook: str | None = None
    inventory_hook: str | None = None
    inventory_formatter: str | None = None
    governance_hook: str | None = None
    rerun_prefix: tuple[str, ...] = ("python", "-m", "scenario_harness", "run")
    environment_paths: dict[str, str] = field(default_factory=dict)
    reserved_environment: tuple[str, ...] = ()

    @property
    def scenarios_dir(self) -> Path:
        return self.suite_root / "scenarios"

    @property
    def runs_dir(self) -> Path:
        return self.suite_root / "runs"

    def replace_suite_root(self, path: Path) -> ProjectSpec:
        return replace(self, suite_root=path.expanduser().resolve())

    def collect(self) -> Any:
        if self.discovery_hook:
            return resolve_reference(self.discovery_hook, self.root)(self)
        from .discovery import collect_scenarios

        return collect_scenarios(
            self.suite_root, base_class=self.base_class, entrypoint=self.entrypoint
        )

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        for name in ("root", "suite_root", "config_path"):
            value[name] = str(value[name]) if value[name] is not None else None
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProjectSpec:
        values = dict(value)
        allowed = cls.__dataclass_fields__
        if set(values) - set(allowed):
            raise InventoryError("Unknown project configuration fields")
        for name in ("root", "suite_root"):
            raw = values.get(name)
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise InventoryError(f"Project {name} must be an absolute path")
            values[name] = Path(raw)
        raw_config = values.get("config_path")
        if raw_config is not None:
            if not isinstance(raw_config, str) or not Path(raw_config).is_absolute():
                raise InventoryError("Project config_path must be absolute")
            values["config_path"] = Path(raw_config)
        for name in ("rerun_prefix", "reserved_environment"):
            raw = values.get(name, getattr(cls, name))
            if not isinstance(raw, list | tuple) or not all(
                isinstance(item, str) for item in raw
            ):
                raise InventoryError(f"Project {name} must contain strings")
            values[name] = tuple(raw)
        paths = values.get("environment_paths", {})
        if not isinstance(paths, dict) or not all(
            isinstance(name, str)
            and name.isidentifier()
            and target in ("root", "suite_root")
            for name, target in paths.items()
        ):
            raise InventoryError("Invalid project environment path mapping")
        for name, raw in values.items():
            if name in {
                "root",
                "suite_root",
                "config_path",
                "rerun_prefix",
                "reserved_environment",
                "environment_paths",
            }:
                continue
            optional = {
                "base_class",
                "discovery_hook",
                "inventory_hook",
                "inventory_formatter",
                "governance_hook",
            }
            if (raw is None and name not in optional) or (
                raw is not None and (not isinstance(raw, str) or not raw)
            ):
                raise InventoryError(f"Project {name} must be nonempty text")
        return cls(**values)


def load_project(
    config_path: str | Path, suite_root: Path | None = None
) -> ProjectSpec:
    config = Path(config_path).expanduser().resolve()
    with config.open("rb") as stream:
        values = tomllib.load(stream)
    if values.pop("schema_version", None) != 1:
        raise InventoryError("Unsupported project schema_version")
    environment_name = values.pop("suite_root_environment", None)
    for name, default in (("root", "."), ("suite_root", ".")):
        raw = values.get(name, default)
        if not isinstance(raw, str):
            raise InventoryError(f"Project {name} must be text")
        values[name] = str((config.parent / raw).resolve())
    values["config_path"] = str(config)
    values.setdefault(
        "rerun_prefix",
        [sys.executable, "-m", "scenario_harness", "--project", str(config), "run"],
    )
    project = ProjectSpec.from_dict(values)
    if suite_root is not None:
        return project.replace_suite_root(suite_root)
    if environment_name is not None:
        if not isinstance(environment_name, str) or not environment_name.isidentifier():
            raise InventoryError("Invalid suite_root_environment")
        if configured := os.environ.get(environment_name):
            project = project.replace_suite_root(Path(configured))
    return project
