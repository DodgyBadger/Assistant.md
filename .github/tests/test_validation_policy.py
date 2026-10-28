"""Repository admission policy checks, separate from application behaviors."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
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


def test_trusted_git_audit_has_no_installed_dependency() -> None:
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            str(repo / "validation/support/ci/audit.py"),
            "--repo",
            str(repo),
            "--base-ref",
            "HEAD",
            "--candidate-ref",
            "HEAD",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["review_required"] is False
    assert report["approval_status"] == "not_evaluated"
