"""Exercise the staging policy in real, isolated pytest subprocesses."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[2]


def _project(tmp_path: Path) -> tuple[Path, Path, Path]:
    validation = tmp_path / "validation"
    stage = validation / "scenarios"
    core = stage / "integration/core/test_behavior.py"
    fault = stage / "experiments/test_fault.py"
    core.parent.mkdir(parents=True)
    fault.parent.mkdir(parents=True)
    core.write_text("def test_first(): pass\ndef test_second(): pass\n")
    fault.write_text("def test_fault(): raise RuntimeError('intentional fault')\n")
    config = stage / "pytest.ini"
    config.write_text(
        "[pytest]\ntestpaths = missing-relative-default\naddopts = -p validation.support.pytest_policy\n"
    )
    registry = validation / "registry"
    registry.mkdir()
    entries = []
    for path, kind, tests in [
        (core, "behavioral", ["test_first", "test_second"]),
        (fault, "behavioral", ["test_fault"]),
        (config, "support", None),
    ]:
        entry: dict[str, object] = {
            "path": path.relative_to(validation).as_posix(),
            "kind": kind,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if tests is not None:
            entry["tests"] = tests
        entries.append(entry)
    (registry / "pytest-admission.json").write_text(
        json.dumps({"schema_version": 1, "files": entries})
    )
    return config, core, fault


def _run(config: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    environment["PYTHONPATH"] = str(_REPOSITORY)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(config),
            "--collect-only",
            "-q",
            *args,
        ],
        cwd=cwd,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("from_staging", [False, True])
def test_default_collection_is_core_only(tmp_path: Path, from_staging: bool) -> None:
    config, _, _ = _project(tmp_path)
    # A repository-wide fallback would import this candidate and raise.
    (tmp_path / "test_unrelated.py").write_text(
        "raise RuntimeError('unexpected repository import')\n"
    )
    result = _run(config, config.parent if from_staging else tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 tests collected" in result.stdout
    assert "test_fault" not in result.stdout


@pytest.mark.parametrize("target", ["file", "node"])
def test_explicit_fault_selection_is_allowed(tmp_path: Path, target: str) -> None:
    config, _, fault = _project(tmp_path)
    selection = str(fault) + ("::test_fault" if target == "node" else "")
    result = _run(config, tmp_path, selection)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout


def test_fault_directory_selection_is_rejected(tmp_path: Path) -> None:
    config, _, fault = _project(tmp_path)
    result = _run(config, tmp_path, str(fault.parent))
    assert result.returncode == 4
    assert "not an admitted core directory" in result.stderr


@pytest.mark.parametrize("selection", ["file", "directory"])
def test_explicit_external_target_is_rejected_before_import(
    tmp_path: Path, selection: str
) -> None:
    config, _, _ = _project(tmp_path)
    outside = tmp_path / "outside/test_external.py"
    outside.parent.mkdir()
    outside.write_text("raise RuntimeError('must never import')\n")
    result = _run(
        config, tmp_path, str(outside if selection == "file" else outside.parent)
    )
    assert result.returncode == 4
    assert "not an admitted" in result.stderr
    assert "must never import" not in result.stdout + result.stderr


def test_source_change_requires_readmission(tmp_path: Path) -> None:
    config, core, _ = _project(tmp_path)
    core.write_text(core.read_text() + "def test_extra(): pass\n")
    result = _run(config, tmp_path)
    assert result.returncode == 4
    assert "source changed since admission" in result.stderr


def test_matching_hash_does_not_admit_extra_collected_function(tmp_path: Path) -> None:
    config, core, _ = _project(tmp_path)
    core.write_text(core.read_text() + "def test_extra(): pass\n")
    manifest = config.parent.parent / "registry/pytest-admission.json"
    payload = json.loads(manifest.read_text())
    payload["files"][0]["sha256"] = hashlib.sha256(core.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(payload))
    result = _run(config, tmp_path)
    assert result.returncode == 4
    assert "unadmitted node" in result.stderr


def test_unknown_explicit_node_is_rejected(tmp_path: Path) -> None:
    config, core, _ = _project(tmp_path)
    result = _run(config, tmp_path, str(core) + "::test_not_admitted")
    assert result.returncode == 4
    assert "not an admitted test" in result.stderr


def test_generated_run_sources_are_not_collected(tmp_path: Path) -> None:
    validation = tmp_path / "validation"
    artifacts = validation / "runs/saved-evaluation"
    artifacts.mkdir(parents=True)
    (artifacts / "test_saved_source.py").write_text(
        "raise RuntimeError('generated evidence must not be imported')\n"
    )
    (validation / "test_application.py").write_text("def test_output(): pass\n")
    (validation / "conftest.py").write_text(
        (_REPOSITORY / "validation/conftest.py").read_text()
    )
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n")
    result = _run(config, tmp_path, str(validation))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 test collected" in result.stdout
    assert (
        "generated evidence must not be imported" not in result.stdout + result.stderr
    )
