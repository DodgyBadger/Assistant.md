"""Real OCR rendering, filesystem compensation and completion transaction failure."""

import base64
import sqlite3

import pytest

from validation.scenarios.support.mistral_ocr_inputs import (
    configure_ocr,
    ocr_provider,
    run_import,
)
from validation.scenarios.support.storage_failure_inputs import (
    block_first_binary_parent,
)
from validation.scenarios.support.vault_inputs import document_pdf

pytestmark = pytest.mark.scenario


def image_response(server):
    # Only external OCR output is controlled: no live provider credentials or
    # mutable document interpretation. Extractor, renderer, storage and jobs run.
    server.response["pages"][0]["markdown"] = "![Scan](img-0.png)"
    server.response["pages"][0]["images"] = [
        {
            "id": "img-0.png",
            "image_base64": "data:image/png;base64,"
            + base64.b64encode(b"OCR_IMAGE_BYTES").decode(),
        }
    ]


async def test_real_ocr_assets_follow_the_numbered_allocated_namespace(app):
    vault = app.vault("OCRNamespaceVault")
    (vault / "local-fallback.pdf").write_bytes(document_pdf("OCR input"))
    await app.start()
    with ocr_provider(app) as server:
        configure_ocr(app, server)
        image_response(server)
        _, first = await run_import(
            app,
            vault,
            options={
                "strategies": ["pdf_ocr"],
                "capture_ocr_images": True,
                "destination": "",
            },
        )
        assert first.status == "completed"
        first_bytes = {p: (vault / p).read_bytes() for p in first.outputs}
        _, second = await run_import(
            app,
            vault,
            options={
                "strategies": ["pdf_ocr"],
                "capture_ocr_images": True,
                "destination": "",
            },
        )
        assert second.status == "completed"
        assert "local-fallback_1.md" in second.outputs
        image = "assets/local-fallback_1/img-0.png"
        assert (
            image in second.outputs
            and (vault / image).read_bytes() == b"OCR_IMAGE_BYTES"
        )
        assert image in (vault / "local-fallback_1.md").read_text()
        assert all(
            (vault / p).read_bytes() == original for p, original in first_bytes.items()
        )


@pytest.mark.parametrize("user_edit", [False, True])
async def test_later_artifact_failure_compensates_and_preserves_user_changes(
    app, user_edit
):
    vault = app.vault("ImportCompensationVault")
    (vault / "local-fallback.pdf").write_bytes(document_pdf("OCR input"))
    await app.start()
    with ocr_provider(app) as server:
        configure_ocr(app, server)
        image_response(server)
        with block_first_binary_parent(vault, preserve_user_edit=user_edit) as observed:
            job, result = await run_import(
                app,
                vault,
                options={
                    "strategies": ["pdf_ocr"],
                    "capture_ocr_images": True,
                    "destination": "",
                },
            )
        assert observed and result.status == "failed"
        assert not result.outputs
        if user_edit:
            assert (vault / "local-fallback.md").read_text() == "USER_MODIFIED_IMPORT"
            assert "cleanup was incomplete" in result.error
        else:
            assert not (vault / "local-fallback.md").exists()
        assert (vault / "assets/local-fallback").read_text() == "EXTERNAL_PATH_CONFLICT"
        assert (vault / "local-fallback.pdf").exists()
        app.evidence.save_json(
            "compensation-result.json",
            {
                "job": job,
                "status": result.status,
                "outputs": result.outputs,
                "error": result.error,
                "user_edit": user_edit,
            },
        )


async def test_real_job_completion_failure_removes_outputs_and_clears_manifest(app):
    vault = app.vault("ImportFinalizeVault")
    (vault / "local-fallback.pdf").write_bytes(document_pdf("Finalize input"))
    await app.start()
    from core.database import get_system_database_path

    database = get_system_database_path("ingestion_jobs")
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TRIGGER reject_import_completion BEFORE UPDATE OF status ON ingestion_jobs WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'controlled finalization failure'); END"
        )
    try:
        _, result = await run_import(
            app, vault, options={"strategies": ["pdf_text"], "destination": ""}
        )
        assert result.status == "failed" and not result.outputs
        assert not (vault / "local-fallback.md").exists()
        assert (vault / "local-fallback.pdf").exists()
    finally:
        with sqlite3.connect(database) as conn:
            conn.execute("DROP TRIGGER reject_import_completion")
