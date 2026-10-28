"""Public PDF URLs reach the configured OCR provider as document references."""

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.mistral_ocr_inputs import (
    configure_ocr,
    ocr_provider,
)

pytestmark = pytest.mark.scenario


async def test_public_pdf_ocr_only_import_delivers_document_reference(
    app: Application,
) -> None:
    vault = app.vault("DirectOcrImportVault")
    # A public numeric address avoids DNS. The OCR-only application path sends
    # this reference to our loopback provider and never downloads the document.
    source = "https://93.184.216.34/report.pdf"
    with ocr_provider(app) as server:
        await app.start()
        configure_ocr(app, server)
        from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
        from core.ingestion.import_service import ContentImportService
        from core.runtime.state import get_runtime_context

        with use_execution_authority(LOCAL_USER_AUTHORITY):
            service = ContentImportService(str(vault))
            submitted = service.submit(
                sources=source,
                options={
                    "strategies": ["pdf_ocr"],
                    "include_ocr_blocks": True,
                    "extract_ocr_header": True,
                    "ocr_confidence": "page",
                },
            )
            await get_runtime_context().ingestion_worker.run_once()
            result = service.status(job_ids=submitted[0].job_id)[0]
        assert (result.status, result.selected_strategy, result.selected_provider) == (
            "completed",
            "pdf_ocr",
            "mistral",
        )
        assert len(server.calls) == 1
        payload = server.calls[0]["payload"]
        assert payload["document"] == {"type": "document_url", "document_url": source}
        assert payload["include_blocks"] is True and payload["extract_header"] is True
        assert payload["confidence_scores_granularity"] == "page"
        assert any(path.endswith("/ocr.json") for path in result.outputs)
        markdown = next(path for path in result.outputs if path.endswith(".md"))
        assert "# Direct OCR" in (vault / markdown).read_text(encoding="utf-8")
        app.evidence.save_json(
            "direct-ocr-import.json",
            {
                "job_id": submitted[0].job_id,
                "outputs": result.outputs,
                "source": source,
            },
        )
