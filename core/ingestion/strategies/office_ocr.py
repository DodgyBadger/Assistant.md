"""Format-specific Office extraction through the shared Mistral adapter."""

from typing import Any

from core.ingestion.models import ExtractedDocument, RawDocument
from core.ingestion.registry import extractor_registry
from core.ingestion.sources.office import OFFICE_FILE_TYPES
from core.ingestion.strategies.mistral_ocr_common import extract_mistral_document


def _extract_office(
    raw: RawDocument, options: dict[str, Any] | None, extension: str
) -> ExtractedDocument:
    mime, strategy = OFFICE_FILE_TYPES[extension]
    if raw.mime != mime:
        raise ValueError(f"{strategy} requires a {extension} source")
    return extract_mistral_document(
        raw,
        options,
        strategy_id=strategy,
        mime=mime,
        requires_base64_images=True,
    )


def extract_docx_ocr(
    raw: RawDocument, options: dict[str, Any] | None = None
) -> ExtractedDocument:
    return _extract_office(raw, options, ".docx")


def extract_pptx_ocr(
    raw: RawDocument, options: dict[str, Any] | None = None
) -> ExtractedDocument:
    return _extract_office(raw, options, ".pptx")


for extension, extractor in (
    (".docx", extract_docx_ocr),
    (".pptx", extract_pptx_ocr),
):
    mime, strategy = OFFICE_FILE_TYPES[extension]
    extractor_registry.register(mime, extractor)
    extractor_registry.register(f"strategy:{strategy}", extractor)
