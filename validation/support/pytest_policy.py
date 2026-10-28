"""Unprivileged collection admission for the canonical application profile."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from validation.support.pytest_admission import load_admissions

_ADMITTED: pytest.StashKey[dict[Path, set[str]]] = pytest.StashKey()


def _admissions(root: Path) -> dict[Path, set[str]]:
    try:
        admitted: dict[Path, set[str]] = {}
        for entry in load_admissions(root):
            if not entry.hash_matches:
                raise ValueError(f"source changed since admission: {entry.relative}")
            if entry.kind == "behavioral":
                admitted[entry.source] = set(entry.tests)
        return admitted
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise pytest.UsageError(f"Canonical pytest admission: {error}") from error


def pytest_configure(config: pytest.Config) -> None:
    if config.inipath is None:
        raise pytest.UsageError(
            "Canonical pytest policy requires a project configuration"
        )
    if config.inipath.name == "pyproject.toml":
        staging = config.inipath.parent / "validation/scenarios"
    elif config.inipath.parent.name == "scenarios":
        staging = config.inipath.parent.resolve()
    else:
        raise pytest.UsageError(
            "Canonical pytest policy requires the application configuration"
        )
    if not config.getoption("scenario_root", default=None) and not hasattr(
        config, "workerinput"
    ):
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        config.option.scenario_root = str(
            staging.parent / "runs" / f"pytest_{stamp}_{uuid4().hex[:10]}"
        )
    core = staging / "integration/core"
    admitted = _admissions(staging.parent)
    config.stash[_ADMITTED] = admitted
    # Pytest interprets testpaths relative to invocation cwd. Normalize defaults
    # before collection so a staging config cannot fall back to the repository.
    if config.args_source != pytest.Config.ArgsSource.ARGS:
        config.args[:] = [str(core)]
    for argument in config.args:
        raw_path, *selection = argument.split("::")
        path = (config.invocation_params.dir / raw_path).resolve()
        if path.is_dir():
            if not path.is_relative_to(core) or not any(
                source.is_relative_to(path) for source in admitted
            ):
                raise pytest.UsageError(
                    f"Canonical pytest target is not an admitted core directory: {argument}"
                )
        elif path not in admitted:
            raise pytest.UsageError(
                f"Canonical pytest target is not an admitted behavioral file: {argument}"
            )
        elif selection and "::".join(selection).split("[", 1)[0] not in admitted[path]:
            raise pytest.UsageError(
                f"Canonical pytest target is not an admitted test: {argument}"
            )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    admitted = config.stash[_ADMITTED]
    for item in items:
        identity = "::".join(item.nodeid.split("::")[1:]).split("[", 1)[0]
        if identity not in admitted.get(item.path.resolve(), set()):
            raise pytest.UsageError(
                f"Canonical pytest collected an unadmitted node: {item.nodeid}"
            )
