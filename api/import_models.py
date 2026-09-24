from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ImportScanRequest(BaseModel):
    vault: str
    queue_only: bool = False
    strategies: list[str] | None = None  # Optional per-run strategy override
    capture_ocr_images: bool | None = (
        None  # Optional per-run OCR image capture override
    )
    pdf_mode: str | None = (
        None  # Optional per-run PDF mode override: markdown|page_images
    )
    include_ocr_blocks: bool | None = None
    ocr_table_format: str | None = None
    extract_ocr_header: bool | None = None
    extract_ocr_footer: bool | None = None
    ocr_confidence: str | None = None


class ImportJobRequestOptions(BaseModel):
    """User-facing options captured when an import job was submitted."""

    destination: str = ""
    strategies: list[str] | None = None
    pdf_strategies: list[str] | None = None
    pdf_mode: str | None = None
    capture_ocr_images: bool | None = None
    clean_html: bool | None = None
    include_ocr_blocks: bool | None = None
    ocr_table_format: str | None = None
    extract_ocr_header: bool | None = None
    extract_ocr_footer: bool | None = None
    ocr_confidence: str | None = None


class ImportJobInfo(BaseModel):
    id: int
    source_uri: str
    vault: str
    source_type: str
    status: str
    error: str | None = None
    outputs: list[str] | None = None
    selected_strategy: str | None = None
    selected_provider: str | None = None
    selected_model: str | None = None
    strategy_attempts: list[str] | None = None
    fallback_reason: str | None = None
    can_resubmit: bool = False
    request_options: ImportJobRequestOptions = Field(
        default_factory=ImportJobRequestOptions
    )
    created_at: datetime
    updated_at: datetime


class ImportJobListResponse(BaseModel):
    jobs: list[ImportJobInfo]
    next_cursor: str | None = None
    total_matching: int
    status_counts: dict[str, int]


class ImportJobCancelResponse(BaseModel):
    job: ImportJobInfo
    cancelled: bool


class ImportRunNowResponse(BaseModel):
    accepted: bool
    queued_count: int
    triggered_at: datetime


class ImportScanResponse(BaseModel):
    jobs_created: list[ImportJobInfo]
    skipped: list[str]


class ImportSourcesRequest(BaseModel):
    vault: str
    sources: list[str] = Field(min_length=1)
    destination: str | None = None
    queue_only: bool = False
    clean_html: bool = True
    strategies: list[str] | None = None
    pdf_strategies: list[str] | None = None
    capture_ocr_images: bool | None = None
    pdf_mode: str | None = None
    include_ocr_blocks: bool | None = None
    ocr_table_format: str | None = None
    extract_ocr_header: bool | None = None
    extract_ocr_footer: bool | None = None
    ocr_confidence: str | None = None


class ImportSourcesResponse(BaseModel):
    jobs_created: list[ImportJobInfo]


class ImportUrlRequest(BaseModel):
    vault: str
    url: str
    clean_html: bool = True
    strategies: list[str] | None = None
    pdf_strategies: list[str] | None = None
    capture_ocr_images: bool | None = None
    pdf_mode: str | None = None
    include_ocr_blocks: bool | None = None
    ocr_table_format: str | None = None
    extract_ocr_header: bool | None = None
    extract_ocr_footer: bool | None = None
    ocr_confidence: str | None = None


class ImportUrlResponse(ImportJobInfo):
    pass
