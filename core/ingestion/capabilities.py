"""Backend-derived ingestion strategy availability metadata."""

from __future__ import annotations

from dataclasses import dataclass

from core.settings.secrets_store import secret_has_value
from core.settings.store import get_general_settings


@dataclass(frozen=True)
class IngestionCapability:
    """Availability and feature metadata for one ingestion strategy."""

    available: bool
    provider: str
    missing: tuple[str, ...]
    features: tuple[str, ...]
    default_order: tuple[str, ...]


def get_mistral_ocr_capability() -> IngestionCapability:
    """Return provider readiness independently of PDF import preferences."""
    settings = get_general_settings()
    model = str(getattr(settings.get("ingestion_ocr_model"), "value", "") or "").strip()
    endpoint = str(
        getattr(settings.get("ingestion_ocr_endpoint"), "value", "") or ""
    ).strip()
    missing: list[str] = []
    if not secret_has_value("MISTRAL_API_KEY"):
        missing.append("MISTRAL_API_KEY")
    if not model:
        missing.append("OCR model")
    if not endpoint:
        missing.append("OCR endpoint")
    return IngestionCapability(
        available=not missing,
        provider="mistral",
        missing=tuple(missing),
        features=(
            "images",
            "blocks",
            "tables",
            "headers",
            "footers",
            "confidence",
        ),
        default_order=(),
    )


def get_pdf_ocr_capability() -> IngestionCapability:
    """Return authoritative availability for the built-in PDF OCR strategy."""
    provider = get_mistral_ocr_capability()
    settings = get_general_settings()
    raw_strategies = getattr(
        settings.get("ingestion_pdf_default_strategies"), "value", []
    )
    strategies = (
        [str(strategy).strip() for strategy in raw_strategies]
        if isinstance(raw_strategies, list)
        else []
    )
    missing = list(provider.missing)
    if "pdf_ocr" not in strategies:
        missing.append("pdf_ocr strategy")
    return IngestionCapability(
        available=not missing,
        provider=provider.provider,
        missing=tuple(missing),
        features=provider.features,
        default_order=tuple(strategies),
    )
