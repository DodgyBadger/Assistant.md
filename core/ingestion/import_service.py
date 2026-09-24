"""Vault-bound submission and status contracts for durable content import."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.ingestion.jobs import IngestionJob, IngestionJobCreate
from core.ingestion.models import SourceKind
from core.ingestion.registry import importer_registry
from core.runtime.state import get_runtime_context
from core.settings.store import get_general_settings
from core.tools.utils import validate_and_resolve_path

DEFAULT_CONTENT_IMPORT_MAX_BATCH_SIZE = 20
_ALLOWED_OPTION_KEYS = {
    "capture_ocr_images",
    "clean_html",
    "destination",
    "extract_ocr_footer",
    "extract_ocr_header",
    "include_ocr_blocks",
    "ocr_confidence",
    "ocr_table_format",
    "pdf_mode",
    "pdf_strategies",
    "strategies",
}
_ALLOWED_PDF_MODES = {"markdown", "page_images"}
_ALLOWED_OCR_TABLE_FORMATS = {"markdown", "html"}
_ALLOWED_OCR_CONFIDENCE = {"page", "word"}


def supported_file_extensions() -> list[str]:
    """Return the registered vault-file import extensions."""
    return sorted(key for key in importer_registry.keys() if key.startswith("."))


def translate_ocr_options(options: dict[str, Any]) -> dict[str, Any]:
    """Validate public OCR enrichment options and return extractor options."""
    translated: dict[str, Any] = {}
    for public_name, internal_name in (
        ("include_ocr_blocks", "ocr_include_blocks"),
        ("extract_ocr_header", "ocr_extract_header"),
        ("extract_ocr_footer", "ocr_extract_footer"),
    ):
        if public_name not in options:
            continue
        value = options[public_name]
        if not isinstance(value, bool):
            raise ValueError(f"{public_name} must be a boolean")
        translated[internal_name] = value
    for public_name, internal_name, allowed in (
        ("ocr_table_format", "ocr_table_format", _ALLOWED_OCR_TABLE_FORMATS),
        ("ocr_confidence", "ocr_confidence", _ALLOWED_OCR_CONFIDENCE),
    ):
        value = options.get(public_name)
        if value is None:
            continue
        normalized = str(value).strip().lower()
        if normalized not in allowed:
            choices = " or ".join(sorted(allowed))
            raise ValueError(f"{public_name} must be {choices}")
        translated[internal_name] = normalized
    return translated


@dataclass(frozen=True)
class ContentImportRequest:
    """One validated source submission."""

    source: str
    source_kind: str
    source_uri: str
    job_options: dict[str, Any]


@dataclass(frozen=True)
class ContentImportResult:
    """Stable tool-facing ingestion job projection."""

    job_id: int
    source: str
    source_kind: str
    status: str
    outputs: list[str]
    error: str | None
    selected_strategy: str | None
    selected_provider: str | None
    selected_model: str | None
    strategy_attempts: list[str]
    fallback_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "source": self.source,
            "source_kind": self.source_kind,
            "status": self.status,
            "outputs": list(self.outputs),
            "error": self.error,
            "selected_strategy": self.selected_strategy,
            "selected_provider": self.selected_provider,
            "selected_model": self.selected_model,
            "strategy_attempts": list(self.strategy_attempts),
            "fallback_reason": self.fallback_reason,
        }


class ContentImportService:
    """Submit and inspect ingestion jobs within one bound vault."""

    def __init__(self, vault_path: str) -> None:
        runtime = get_runtime_context()
        self._data_root = Path(runtime.config.data_root).resolve()
        self._vault_path = Path(vault_path).resolve()
        try:
            relative_vault = self._vault_path.relative_to(self._data_root)
        except ValueError as exc:
            raise ValueError("vault_path must be inside configured data_root") from exc
        if len(relative_vault.parts) != 1:
            raise ValueError("vault_path must identify one vault under data_root")
        self._vault_name = relative_vault.parts[0]
        self._ingestion = runtime.ingestion

    @property
    def vault_name(self) -> str:
        return self._vault_name

    def submit(
        self,
        *,
        sources: str | list[str],
        options: dict[str, Any] | None = None,
    ) -> list[ContentImportResult]:
        normalized_sources = self._normalize_sources(sources)
        job_options = self._validate_options(options)
        requests = [
            self._validate_source(source=source, job_options=job_options)
            for source in normalized_sources
        ]
        jobs = self._ingestion.enqueue_jobs(
            [
                IngestionJobCreate(
                    source_uri=request.source_uri,
                    vault=self._vault_name,
                    source_type=(
                        SourceKind.URL.value
                        if request.source_kind == "url"
                        else SourceKind.FILE.value
                    ),
                    mime_hint=None,
                    options=request.job_options,
                )
                for request in requests
            ],
            vault=self._vault_name,
        )
        return [
            self._serialize_job(job, source_kind=request.source_kind)
            for request, job in zip(requests, jobs, strict=True)
        ]

    def status(self, *, job_ids: int | list[int]) -> list[ContentImportResult]:
        normalized_ids = self._normalize_job_ids(job_ids)
        jobs: list[IngestionJob] = []
        for job_id in normalized_ids:
            job = self._ingestion.get_job(job_id)
            if job is None or job.vault != self._vault_name:
                raise ValueError(f"Ingestion job {job_id} was not found in this vault")
            jobs.append(job)
        return [self._serialize_job(job) for job in jobs]

    def _normalize_sources(self, sources: str | list[str]) -> list[str]:
        raw_sources = [sources] if isinstance(sources, str) else list(sources)
        if not raw_sources:
            raise ValueError("sources must contain at least one source")
        max_batch_size = get_content_import_max_batch_size()
        if len(raw_sources) > max_batch_size:
            raise ValueError(
                f"sources exceeds content import batch limit of {max_batch_size}"
            )
        normalized: list[str] = []
        for raw_source in raw_sources:
            if not isinstance(raw_source, str) or not raw_source.strip():
                raise ValueError("each source must be a non-empty string")
            normalized.append(raw_source.strip())
        return normalized

    def _validate_source(
        self,
        *,
        source: str,
        job_options: dict[str, Any],
    ) -> ContentImportRequest:
        parsed = urlsplit(source)
        if parsed.scheme:
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                raise ValueError("URL sources must use HTTP or HTTPS")
            return ContentImportRequest(
                source=source,
                source_kind="url",
                source_uri=source,
                job_options={**job_options, "consume_source": False},
            )

        resolved = Path(
            validate_and_resolve_path(
                source,
                str(self._vault_path),
                markdown_only=False,
            )
        )
        if not resolved.is_file():
            raise ValueError(f"Vault file source does not exist: {source}")
        supported_extensions = set(supported_file_extensions())
        if resolved.suffix.lower() not in supported_extensions:
            raise ValueError(f"Vault file source type is not supported: {source}")
        relative_source = resolved.relative_to(self._vault_path).as_posix()
        return ContentImportRequest(
            source=source,
            source_kind="vault_file",
            source_uri=relative_source,
            job_options={**job_options, "consume_source": False},
        )

    def _validate_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        if options is None:
            options = {}
        if not isinstance(options, dict):
            raise ValueError("options must be an object")
        unknown = sorted(set(options) - _ALLOWED_OPTION_KEYS)
        if unknown:
            raise ValueError(
                f"Unsupported content import option(s): {', '.join(unknown)}"
            )

        translated: dict[str, Any] = {}
        general_settings = get_general_settings()

        def setting_value(key: str, fallback: Any) -> Any:
            entry = general_settings.get(key)
            return fallback if entry is None else entry.value

        destination = options.get(
            "destination",
            setting_value("ingestion_output_path_pattern", "Imported/"),
        )
        if destination is not None:
            if not isinstance(destination, str):
                raise ValueError("destination must be a vault-relative path")
            normalized_destination = destination.strip()
            if normalized_destination in {"", "."}:
                translated["output_path_pattern"] = ""
                normalized_destination = ""
            if not normalized_destination:
                destination = None
            else:
                destination = normalized_destination
        if destination is not None:
            resolved_destination = Path(
                validate_and_resolve_path(
                    destination,
                    str(self._vault_path),
                    markdown_only=False,
                )
            )
            if resolved_destination.exists() and not resolved_destination.is_dir():
                raise ValueError("destination must identify a vault directory")
            relative_destination = resolved_destination.relative_to(
                self._vault_path
            ).as_posix()
            translated["output_path_pattern"] = relative_destination

        pdf_mode = options.get(
            "pdf_mode", setting_value("ingestion_pdf_default_mode", "markdown")
        )
        if pdf_mode is not None:
            normalized_pdf_mode = str(pdf_mode).strip().lower()
            if normalized_pdf_mode not in _ALLOWED_PDF_MODES:
                raise ValueError("pdf_mode must be markdown or page_images")
            translated["pdf_mode"] = normalized_pdf_mode

        for option_name in ("strategies", "pdf_strategies"):
            strategies = options.get(option_name)
            if (
                option_name == "pdf_strategies"
                and strategies is None
                and options.get("strategies") is None
            ):
                strategies = setting_value(
                    "ingestion_pdf_default_strategies", ["pdf_ocr", "pdf_text"]
                )
            if strategies is None:
                continue
            if not isinstance(strategies, list) or not strategies:
                raise ValueError(f"{option_name} must be a non-empty list")
            normalized_strategies: list[str] = []
            for strategy in strategies:
                if not isinstance(strategy, str) or not strategy.strip():
                    raise ValueError(
                        f"each {option_name} item must be a non-empty string"
                    )
                normalized_strategies.append(strategy.strip())
            translated[option_name] = normalized_strategies

        extractor_options: dict[str, Any] = {}
        if "capture_ocr_images" not in options:
            extractor_options["ocr_capture_images"] = bool(
                setting_value("ingestion_ocr_capture_images", False)
            )
        for public_name, internal_name in (
            ("capture_ocr_images", "ocr_capture_images"),
            ("clean_html", "clean_html"),
        ):
            if public_name not in options:
                continue
            value = options[public_name]
            if not isinstance(value, bool):
                raise ValueError(f"{public_name} must be a boolean")
            extractor_options[internal_name] = value
        extractor_options.update(translate_ocr_options(options))
        if extractor_options:
            translated["extractor_options"] = extractor_options
        return translated

    @staticmethod
    def _normalize_job_ids(job_ids: int | list[int]) -> list[int]:
        raw_ids = [job_ids] if isinstance(job_ids, int) else list(job_ids)
        if not raw_ids:
            raise ValueError("job_ids must contain at least one job id")
        max_batch_size = get_content_import_max_batch_size()
        if len(raw_ids) > max_batch_size:
            raise ValueError(
                f"job_ids exceeds content import batch limit of {max_batch_size}"
            )
        normalized: list[int] = []
        for job_id in raw_ids:
            if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
                raise ValueError("each job id must be a positive integer")
            normalized.append(job_id)
        return normalized

    @staticmethod
    def _serialize_job(
        job: IngestionJob,
        *,
        source_kind: str | None = None,
    ) -> ContentImportResult:
        resolved_kind = source_kind or (
            "url" if job.source_type == SourceKind.URL.value else "vault_file"
        )
        return ContentImportResult(
            job_id=job.id,
            source=job.source_uri,
            source_kind=resolved_kind,
            status=job.status,
            outputs=list(job.outputs or []),
            error=job.error,
            selected_strategy=job.selected_strategy,
            selected_provider=job.selected_provider,
            selected_model=job.selected_model,
            strategy_attempts=list(job.strategy_attempts or []),
            fallback_reason=job.fallback_reason,
        )


def get_content_import_max_batch_size() -> int:
    """Return the configured positive content-import batch limit."""
    entry = get_general_settings().get("content_import_max_batch_size")
    value = entry.value if entry is not None else DEFAULT_CONTENT_IMPORT_MAX_BATCH_SIZE
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "content_import_max_batch_size must be a positive integer"
        ) from exc
    if parsed <= 0:
        raise ValueError("content_import_max_batch_size must be a positive integer")
    return parsed
