"""Focused tests for collision-consistent, compensated import storage."""

import base64
from pathlib import Path

import pytest

from core.ingestion.models import ExtractedDocument, RenderOptions
from core.ingestion.output_paths import allocate_import_output_paths
from core.ingestion.renderers import default_renderer
from core.ingestion.service import IngestionService
from core.ingestion.storage import StoredArtifact, default_storage
from core.utils.hash import hash_bytes, hash_file_bytes
from core.vault_state.file_mutations import VaultMutationRejected


def _restore_exact_file_states(*, vault_path: Path, states: tuple) -> tuple:
    for state in states:
        target = Path(vault_path) / state.path
        if (
            not target.exists()
            or hash_file_bytes(target, length=None) != state.expected_sha256
        ):
            raise VaultMutationRejected("file_conflict", "current file state changed")
        target.unlink()
    return ()


def test_output_allocation_uses_one_numbered_namespace(tmp_path: Path) -> None:
    vault = tmp_path / "Vault"
    (vault / "Imported").mkdir(parents=True)
    (vault / "Imported" / "report.md").write_text("existing", encoding="utf-8")

    with allocate_import_output_paths(
        vault_root=vault,
        path_pattern="Imported",
        relative_dir="",
        source_filename="report.pdf",
        title=None,
    ) as paths:
        assert paths.markdown_path == "Imported/report_1.md"
        assert paths.asset_dir == "Imported/assets/report_1"


def test_renderer_links_ocr_assets_to_the_allocated_namespace() -> None:
    document = ExtractedDocument(
        plain_text="![Scan](img-0.png)",
        mime="application/pdf",
        strategy_id="pdf_ocr",
        meta={
            "ocr_images": [
                {
                    "data_base64": base64.b64encode(b"image").decode("ascii"),
                    "media_type": "image/png",
                    "page_number": 1,
                    "image_index": 1,
                }
            ]
        },
    )

    rendered = default_renderer(
        document,
        RenderOptions(
            path_pattern="Imported",
            source_filename="report.pdf",
            output_name="report_1",
        ),
    )

    assert rendered[0]["path"] == "Imported/report_1.md"
    assert "assets/report_1/page_0001_img_01.png" in rendered[0]["content"]
    assert rendered[1]["path"] == "Imported/assets/report_1/page_0001_img_01.png"


def test_storage_compensates_prior_outputs_when_a_later_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()
    monkeypatch.setattr("core.ingestion.storage.get_data_root", lambda: str(tmp_path))

    def write_text(*, vault_path: Path, path: str, content: str, **_kwargs) -> None:
        target = Path(vault_path) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def write_bytes_then_raise(
        *, vault_path: Path, path: str, content: bytes, **_kwargs
    ) -> None:
        target = Path(vault_path) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        raise RuntimeError("injected post-write failure")

    monkeypatch.setattr("core.ingestion.storage.write_vault_file", write_text)
    monkeypatch.setattr(
        "core.ingestion.storage.write_vault_file_bytes",
        write_bytes_then_raise,
    )
    monkeypatch.setattr(
        "core.ingestion.storage.restore_file_states_atomically",
        _restore_exact_file_states,
    )

    with pytest.raises(RuntimeError, match="injected post-write failure"):
        default_storage(
            [
                {"path": "Imported/report.md", "content": "markdown"},
                {"path": "Imported/assets/report/image.png", "content_bytes": b"x"},
            ],
            RenderOptions(vault="Vault"),
        )

    assert not (vault / "Imported/report.md").exists()
    assert not (vault / "Imported/assets/report/image.png").exists()


def test_storage_preserves_an_artifact_modified_before_compensation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()
    monkeypatch.setattr("core.ingestion.storage.get_data_root", lambda: str(tmp_path))

    def write_text(*, vault_path: Path, path: str, content: str, **_kwargs) -> None:
        target = Path(vault_path) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def fail_after_external_edit(**_kwargs) -> None:
        (vault / "Imported/report.md").write_text("user edit", encoding="utf-8")
        raise RuntimeError("later write failed")

    monkeypatch.setattr("core.ingestion.storage.write_vault_file", write_text)
    monkeypatch.setattr(
        "core.ingestion.storage.write_vault_file_bytes", fail_after_external_edit
    )
    monkeypatch.setattr(
        "core.ingestion.storage.restore_file_states_atomically",
        _restore_exact_file_states,
    )

    with pytest.raises(RuntimeError, match="cleanup was incomplete"):
        default_storage(
            [
                {"path": "Imported/report.md", "content": "markdown"},
                {"path": "Imported/assets/report/image.png", "content_bytes": b"x"},
            ],
            RenderOptions(vault="Vault"),
        )

    assert (vault / "Imported/report.md").read_text(encoding="utf-8") == "user edit"


def test_job_finalization_failure_removes_written_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "Vault"
    vault.mkdir()
    output = vault / "Imported/report.md"
    output.parent.mkdir()
    output.write_text("markdown", encoding="utf-8")
    manifests: list[list[str]] = []

    def fail_completion(_job_id: int, paths: list[str]) -> None:
        manifests.append(list(paths))
        raise RuntimeError("injected completion failure")

    monkeypatch.setattr(
        "core.ingestion.storage.restore_file_states_atomically",
        _restore_exact_file_states,
    )
    monkeypatch.setattr(
        "core.ingestion.service.complete_job",
        fail_completion,
    )
    monkeypatch.setattr(
        "core.ingestion.service.update_job_outputs",
        lambda _job_id, paths: manifests.append(list(paths)),
    )
    service = object.__new__(IngestionService)
    service.logger = type("Logger", (), {"warning": lambda *_args, **_kwargs: None})()

    with pytest.raises(RuntimeError, match="injected completion failure"):
        service._finalize_written_outputs(
            job_id=42,
            vault_root=vault,
            artifacts=[
                StoredArtifact(
                    path="Imported/report.md",
                    sha256=hash_bytes(b"markdown", length=None),
                )
            ],
        )

    assert manifests == [["Imported/report.md"], []]
    assert not output.exists()
