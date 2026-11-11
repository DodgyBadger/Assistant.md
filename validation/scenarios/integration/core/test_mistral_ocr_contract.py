"""Real OCR transport, enrichment assets and provider failures through ingestion."""

import json

import pytest

from validation.support.application import Application
from validation.support.integrations.mistral_ocr_inputs import (
    OCR_KEY,
    PRIVATE_DOCUMENT,
    configure_ocr,
    ocr_provider,
    run_import,
    terminal_activity,
)
from validation.support.runtime.ingestion_inputs import (
    pdf_source,
    require_missing_ocr_secret,
)

pytestmark = pytest.mark.scenario


async def test_ocr_enrichment_and_direct_document_transport(app: Application) -> None:
    vault = app.vault("MistralOcrContractVault")
    pdf_source(vault, "local-fallback.pdf", "Local extraction fallback")
    with ocr_provider(app) as server:
        await app.start()
        configure_ocr(app, server)
        metadata = app.request("/api/metadata")
        assert metadata.status_code == 200
        capability = metadata.json()["ingestion_capabilities"]["pdf_ocr"]
        assert capability["available"] is True
        assert "blocks" in capability["features"]
        assert capability["default_order"] == ["pdf_ocr", "pdf_text"]
        _, result = await run_import(
            app,
            vault,
            options={
                "strategies": ["pdf_ocr"],
                "include_ocr_blocks": True,
                "extract_ocr_header": True,
                "ocr_confidence": "page",
            },
        )
        assert (result.status, result.selected_strategy, result.selected_provider) == (
            "completed",
            "pdf_ocr",
            "mistral",
        )
        request = server.calls[0]
        assert request["authorization"] == f"Bearer {OCR_KEY}"
        assert request["payload"]["document"]["document_url"].startswith(
            "data:application/pdf;base64,"
        )
        assert request["payload"]["include_image_base64"] is False
        assert "image_limit" not in request["payload"]
        assert request["payload"]["include_blocks"] is True
        assert request["payload"]["extract_header"] is True
        assert request["payload"]["confidence_scores_granularity"] == "page"
        asset = next(path for path in result.outputs if path.endswith("/ocr.json"))
        structured = json.loads((vault / asset).read_text())
        assert "Report header" in json.dumps(structured)
        assert "average_page_confidence_score" in json.dumps(structured)

        # A raw extractor input verifies direct-document serialization. It does
        # not establish public-URL admission by the complete import service.
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.ingestion.models import RawDocument, SourceKind
        from core.ingestion.strategies.pdf_ocr import extract_pdf_ocr

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            extracted = extract_pdf_ocr(
                RawDocument(
                    source_uri="https://example.org/report.pdf",
                    kind=SourceKind.URL,
                    mime="application/pdf",
                    payload=b"",
                    meta={"ocr_document_url": "https://example.org/report.pdf"},
                ),
                {
                    "ocr_include_blocks": True,
                    "ocr_extract_header": True,
                    "ocr_confidence": "page",
                },
            )
        assert extracted.plain_text == "# Direct OCR"
        assert extracted.meta["transport"] == "direct_url"
        assert server.calls[-1]["payload"]["document"] == {
            "type": "document_url",
            "document_url": "https://example.org/report.pdf",
        }


async def test_ocr_provider_rejections_and_local_fallback(app: Application) -> None:
    vault = app.vault("MistralOcrFailureVault")
    pdf_source(vault, "local-fallback.pdf", "Local extraction fallback")
    with ocr_provider(app) as server:
        await app.start()
        configure_ocr(app, server)
        for status, message, code in (
            (400, "Invalid model: mistral-ocr-4-0", "1500"),
            (429, "Rate limit exceeded", "rate_limit"),
            (401, f"Invalid API key: {OCR_KEY}", "unauthorized"),
            (502, "No structured provider error message was returned.", ""),
        ):
            server.status = status
            server.response = (
                {
                    "message": message,
                    "type": "invalid_model" if status == 400 else "rate_limit",
                    "code": code,
                    "document": PRIVATE_DOCUMENT,
                }
                if status != 502
                else f"<html>{PRIVATE_DOCUMENT}</html>"
            )
            job_id, result = await run_import(
                app, vault, options={"strategies": ["pdf_ocr"]}
            )
            assert result.status == "failed"
            terminal = terminal_activity(app, job_id, "ingestion_job_failed")
            assert message.replace(OCR_KEY, "[redacted]") in terminal["error"]
            failure = terminal["extractor_failures"][0]
            assert (
                failure["strategy"],
                failure["provider"],
                failure["http_status"],
            ) == ("pdf_ocr", "mistral", status)
            if code:
                assert failure["provider_error_code"] == code
            assert terminal["vault_name"] == vault.name
            assert OCR_KEY not in json.dumps(terminal)
            assert PRIVATE_DOCUMENT not in json.dumps(terminal)
        server.status = 400
        server.response = {"message": "Invalid model: unavailable"}
        job_id, fallback = await run_import(
            app, vault, options={"strategies": ["pdf_ocr", "pdf_text"]}
        )
        assert (
            fallback.status == "completed" and fallback.selected_strategy == "pdf_text"
        )
        terminal = terminal_activity(app, job_id, "ingestion_job_completed")
        assert (
            "Invalid model: unavailable" in terminal["extractor_failures"][0]["error"]
        )
        count = len(server.calls)
        require_missing_ocr_secret(app)
        _, missing = await run_import(app, vault)
        assert missing.status == "completed" and missing.selected_strategy == "pdf_text"
        assert missing.strategy_attempts == ["pdf_ocr", "pdf_text"]
        assert len(server.calls) == count


@pytest.mark.parametrize("extension", ["docx", "pptx"])
@pytest.mark.parametrize("capture_images", [False, True])
async def test_office_import_preserves_source_and_controls_extracted_images(
    app: Application, extension: str, capture_images: bool
) -> None:
    import base64
    import re
    from pathlib import Path

    from core.runtime.state import get_runtime_context

    vault = app.vault("OfficeImportVault")
    source = vault / f"Uploads/notes.{extension}"
    source.parent.mkdir()
    fixture = Path(__file__).parents[3] / f"fixtures/files/office-import.{extension}"
    original = fixture.read_bytes()
    source.write_bytes(original)
    mime = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if extension == "docx"
        else "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    # Only external OCR output is controlled. Source loading, admission, job
    # processing, authorization, rendering, asset writes and source preservation
    # remain real. Genuine synthetic Office fixtures also back the live probe.
    with ocr_provider(app) as server:
        await app.start()
        configure_ocr(app, server)
        local_only = app.request(
            "/api/system/settings/general/ingestion_pdf_default_strategies",
            "PUT",
            {"value": json.dumps(["pdf_text"])},
        )
        assert local_only.status_code == 200, local_only.text
        server.response["pages"][0][
            "markdown"
        ] = "# Office notes\n\nThe orchard has seven apple trees."
        if capture_images:
            server.response["pages"][0][
                "markdown"
            ] += "\n\n![First](img-1.png)\n\n![Second](img-0.png)"
        server.response["pages"][0]["images"] = [
            {
                "id": "img-1.png",
                "image_base64": "data:image/png;base64,"
                + base64.b64encode(b"OFFICE_IMAGE_A").decode(),
            },
            {
                "id": "img-0.png",
                "image_base64": "data:image/png;base64,"
                + base64.b64encode(b"OFFICE_IMAGE_B").decode(),
            },
        ]
        response = app.request(
            "/api/import/sources",
            "POST",
            {
                "vault": vault.name,
                "sources": [f"Uploads/notes.{extension}"],
                "destination": "Library/Selected",
                "capture_ocr_images": capture_images,
                "queue_only": True,
            },
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["jobs_created"][0]["id"]
        await get_runtime_context().ingestion_worker.run_once()
        status = app.request(f"/api/import/jobs/{job_id}").json()
        assert status["status"] == "completed", status
        assert status["selected_strategy"] == f"{extension}_ocr"
        assert status["selected_provider"] == "mistral"
        assert status["strategy_attempts"] == [f"{extension}_ocr"]
        assert source.read_bytes() == original
        output = vault / next(
            path for path in status["outputs"] if path.endswith(".md")
        )
        assert output.parent == vault / "Library/Selected"
        assert "The orchard has seven apple trees." in output.read_text()
        assert len(server.calls) == 1
        payload = server.calls[0]["payload"]
        document_url = payload["document"]["document_url"]
        assert document_url.startswith(f"data:{mime};base64,")
        assert base64.b64decode(document_url.split(",", 1)[1]) == original
        assert payload["include_image_base64"] is True
        images = [vault / path for path in status["outputs"] if path.endswith(".png")]
        if capture_images:
            assert "image_limit" not in payload
            assert len(images) == 2
            targets = [
                (output.parent / target).resolve()
                for target in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", output.read_text())
            ]
            assert set(targets) == {image.resolve() for image in images}
            assert [target.read_bytes() for target in targets] == [
                b"OFFICE_IMAGE_A",
                b"OFFICE_IMAGE_B",
            ]
        else:
            assert payload["image_limit"] == 0
            assert images == []
        extensions = app.request("/api/metadata").json()["ingestion_capabilities"][
            "file_import"
        ]["features"]
        assert ".docx" in extensions and ".pptx" in extensions
        terminal = terminal_activity(app, job_id, "ingestion_job_completed")
        assert terminal["selected_strategy"] == f"{extension}_ocr"


@pytest.mark.parametrize("extension", ["docx", "pptx"])
async def test_office_import_reports_missing_mistral_configuration(
    app: Application, extension: str
) -> None:
    from pathlib import Path

    from core.runtime.state import get_runtime_context

    vault = app.vault("OfficeMissingCredentialVault")
    source = vault / f"notes.{extension}"
    original = (
        Path(__file__).parents[3] / f"fixtures/files/office-import.{extension}"
    ).read_bytes()
    source.write_bytes(original)
    with ocr_provider(app) as server:
        await app.start()
        response = app.request(
            "/api/import/sources",
            "POST",
            {
                "vault": vault.name,
                "sources": [source.name],
                "queue_only": True,
            },
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["jobs_created"][0]["id"]
        await get_runtime_context().ingestion_worker.run_once()
        status = app.request(f"/api/import/jobs/{job_id}").json()
        assert status["status"] == "failed", status
        assert status["strategy_attempts"] == [f"{extension}_ocr"]
        assert "missing_secret:MISTRAL_API_KEY" in status["fallback_reason"]
        assert source.read_bytes() == original
        assert server.calls == []
