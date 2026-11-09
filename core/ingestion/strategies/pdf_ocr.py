"""Mistral OCR extractor for PDFs."""

from __future__ import annotations

from typing import Any

from core.ingestion.models import ExtractedDocument, RawDocument
from core.ingestion.registry import extractor_registry
from core.ingestion.strategies.mistral_ocr_common import extract_mistral_document


def extract_pdf_ocr(
    raw: RawDocument, options: dict[str, Any] | None = None
) -> ExtractedDocument:
    return extract_mistral_document(
        raw, options, strategy_id="pdf_ocr", mime="application/pdf"
    )


# Register extractor for explicit strategy and MIME so callers can resolve either way.
extractor_registry.register("application/pdf", extract_pdf_ocr)
extractor_registry.register("strategy:pdf_ocr", extract_pdf_ocr)
