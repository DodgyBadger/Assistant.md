"""Real OCR transport, enrichment assets and provider failures through ingestion."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.ingestion_inputs import (
    pdf_source,
    require_missing_ocr_secret,
)
from validation.scenarios.support.mistral_ocr_inputs import (
    OCR_KEY,
    PRIVATE_DOCUMENT,
    configure_ocr,
    ocr_provider,
    run_import,
    terminal_activity,
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
