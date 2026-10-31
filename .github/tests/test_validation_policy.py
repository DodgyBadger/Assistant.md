"""Repository admission policy checks, separate from application behaviors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from validation.support.inventory import (
    InventoryError,
    InventoryReport,
    _check_pytest_admission,
)


def _staging(root: Path, *, kind: str = "behavioral") -> Path:
    source = root / "scenarios/integration/core/test_example.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "raise RuntimeError('inventory must never import candidate code')\n"
        "def test_output(app):\n    assert app.output == 'result'\n"
    )
    registry = root / "registry"
    registry.mkdir()
    (registry / "pytest-admission.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": [
                    {
                        "path": source.relative_to(root).as_posix(),
                        "kind": kind,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "tests": ["test_output"],
                    }
                ],
            }
        )
    )
    return source


def test_admission_reads_declarations_without_import(tmp_path: Path) -> None:
    source = _staging(tmp_path)
    report = InventoryReport()
    admitted = _check_pytest_admission(tmp_path, report)
    assert admitted == {source.relative_to(tmp_path).as_posix()}
    assert report.issues == []
    assert (
        report.pytest_admission[0]["coverage_status"]
        == "registered_pending_parity_review"
    )


def test_new_source_is_not_hidden_by_staging_root(tmp_path: Path) -> None:
    source = _staging(tmp_path)
    source.with_name("test_escaped.py").write_text("def test_other(): pass\n")
    report = InventoryReport()
    _check_pytest_admission(tmp_path, report)
    assert any(
        "Unregistered" in issue and "test_escaped" in issue for issue in report.issues
    )


def test_changed_expectation_requires_readmission(tmp_path: Path) -> None:
    source = _staging(tmp_path)
    source.write_text(source.read_text().replace("'result'", "'changed'"))
    report = InventoryReport()
    _check_pytest_admission(tmp_path, report)
    assert any("changed since admission" in issue for issue in report.issues)


def test_missing_case_is_detected_even_with_updated_hash(tmp_path: Path) -> None:
    source = _staging(tmp_path)
    source.write_text(source.read_text().replace("test_output", "test_replacement"))
    manifest = tmp_path / "registry/pytest-admission.json"
    data = json.loads(manifest.read_text())
    data["files"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    report = InventoryReport()
    _check_pytest_admission(tmp_path, report)
    assert any("declarations differ" in issue for issue in report.issues)


def test_behavioral_test_cannot_be_hidden_as_support(tmp_path: Path) -> None:
    _staging(tmp_path, kind="support")
    with pytest.raises(InventoryError, match="cannot be admitted as support"):
        _check_pytest_admission(tmp_path, InventoryReport())


def test_manifest_cannot_admit_external_source(tmp_path: Path) -> None:
    source = _staging(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-external.py"
    outside.write_text(source.read_text())
    source.unlink()
    source.symlink_to(outside)
    with pytest.raises(InventoryError, match="external"):
        _check_pytest_admission(tmp_path, InventoryReport())


@pytest.mark.parametrize(
    "payload",
    [[], None, {"schema_version": True}, {"schema_version": 1, "files": [None]}],
)
def test_malformed_manifest_is_a_controlled_inventory_error(
    tmp_path: Path, payload: object
) -> None:
    _staging(tmp_path)
    (tmp_path / "registry/pytest-admission.json").write_text(json.dumps(payload))
    with pytest.raises(InventoryError):
        _check_pytest_admission(tmp_path, InventoryReport())


def _support_source(root: Path, *, kind: str = "support") -> Path:
    _staging(root)
    source = root / "support/chat/provider.py"
    source.parent.mkdir(parents=True)
    source.write_text("raise RuntimeError('helper must not be imported')\n")
    manifest = root / "registry/pytest-admission.json"
    data = json.loads(manifest.read_text())
    data["files"].append(
        {
            "path": source.relative_to(root).as_posix(),
            "kind": kind,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "tests": [],
        }
    )
    manifest.write_text(json.dumps(data))
    return source


def test_support_admission_is_import_free_and_detects_changed_helpers(
    tmp_path: Path,
) -> None:
    source = _support_source(tmp_path)
    report = InventoryReport()
    admitted = _check_pytest_admission(tmp_path, report)
    assert source.relative_to(tmp_path).as_posix() in admitted
    assert report.issues == []

    source.write_text("raise RuntimeError('changed helper')\n")
    report = InventoryReport()
    _check_pytest_admission(tmp_path, report)
    assert report.issues == [
        "Pytest source changed since admission: support/chat/provider.py"
    ]


def test_unregistered_support_helper_is_detected(tmp_path: Path) -> None:
    source = _support_source(tmp_path)
    source.with_name("unregistered.py").write_text(
        "raise RuntimeError('not imported')\n"
    )
    report = InventoryReport()
    _check_pytest_admission(tmp_path, report)
    assert report.issues == ["Unregistered pytest source: support/chat/unregistered.py"]


def test_support_directory_cannot_admit_behavioral_cases(tmp_path: Path) -> None:
    _support_source(tmp_path, kind="behavioral")
    with pytest.raises(InventoryError, match="outside admitted roots"):
        _check_pytest_admission(tmp_path, InventoryReport())


@pytest.mark.parametrize("whole_directory", [False, True])
def test_support_admission_rejects_symlink_escape(
    tmp_path: Path, whole_directory: bool
) -> None:
    source = _support_source(tmp_path)
    external = tmp_path / "outside"
    external.mkdir()
    (external / source.name).write_text(source.read_text())
    source.unlink()
    if whole_directory:
        source.parent.rmdir()
        source.parent.symlink_to(external, target_is_directory=True)
    else:
        source.symlink_to(external / source.name)
    with pytest.raises(InventoryError, match="external"):
        _check_pytest_admission(tmp_path, InventoryReport())
