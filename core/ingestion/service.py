"""
Ingestion service wired into runtime.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import yaml

from core.constants import ASSISTANTMD_ROOT_DIR, IMPORT_DIR
from core.ingestion.jobs import (
    IngestionJob,
    claim_queued_job,
    create_job,
    get_job,
    init_db,
    list_jobs,
    update_job_outputs,
    update_job_provenance,
    update_job_status,
)
from core.ingestion.models import (
    ExtractedDocument,
    JobStatus,
    RawDocument,
    RenderMode,
    RenderOptions,
    SourceKind,
)
from core.ingestion.output_paths import resolve_import_output_paths
from core.ingestion.registry import extractor_registry, importer_registry
from core.ingestion.renderers import default_renderer
from core.ingestion.storage import default_storage
from core.logger import UnifiedLogger
from core.runtime.paths import get_data_root
from core.settings.secrets_store import secret_has_value
from core.settings.store import get_general_settings
from core.vault_state.file_mutations import (
    delete_vault_file,
    write_vault_file,
    write_vault_file_bytes,
)
from core.vault_state.pathing import resolve_configured_vault_root
from core.web.security import resolve_public_url, sanitize_url_for_log


class IngestionService:
    _STRATEGY_SECRET_REQUIREMENTS = {
        "pdf_ocr": "MISTRAL_API_KEY",
        "image_ocr": "MISTRAL_API_KEY",
    }

    def __init__(self) -> None:
        self.logger = UnifiedLogger(tag="ingestion")
        self._load_builtin_handlers()
        init_db()

    def enqueue_job(
        self,
        source_uri: str,
        vault: str,
        source_type: str,
        mime_hint: str | None,
        options: dict[str, Any] | None,
    ) -> IngestionJob:
        vault_root = resolve_configured_vault_root(
            data_root=get_data_root(), vault_name=vault
        )
        opts = options or {}
        return create_job(source_uri, vault_root.name, source_type, mime_hint, opts)

    def list_recent_jobs(self, limit: int = 50) -> list[IngestionJob]:
        return list_jobs(limit)

    def get_job(self, job_id: int) -> IngestionJob | None:
        return get_job(job_id)

    def claim_job(self, job_id: int) -> bool:
        """Atomically claim one queued job for processing."""
        return claim_queued_job(job_id)

    def mark_completed(self, job_id: int) -> None:
        update_job_status(job_id, JobStatus.COMPLETED)

    def mark_failed(self, job_id: int, error: str) -> None:
        update_job_status(job_id, JobStatus.FAILED, error)

    def process_job(self, job_id: int) -> None:
        """
        Process a single ingestion job end-to-end.
        """
        job = self.get_job(job_id)
        if job is None:
            raise ValueError(f"Ingestion job {job_id} not found")
        if job.status != JobStatus.PROCESSING.value:
            raise ValueError(
                f"Ingestion job {job_id} must be claimed before processing; "
                f"current status is {job.status}"
            )
        log_context = self._job_log_context(job)
        self.logger.info(
            "ingestion_job_started",
            data={
                **log_context,
                "event": "ingestion_job_started",
                "status": "started",
            },
        )

        ingestion_settings = {}
        try:
            vault = job.vault
            if not vault:
                raise ValueError("Job missing vault")
            ingestion_settings = self._get_ingestion_settings()

            data_root = Path(get_data_root())
            vault_root = resolve_configured_vault_root(
                data_root=data_root, vault_name=vault
            )
            vault = vault_root.name
            import_root = vault_root / ASSISTANTMD_ROOT_DIR / IMPORT_DIR
            legacy_import_root = data_root / vault / ASSISTANTMD_ROOT_DIR / "import"
            import_root.mkdir(parents=True, exist_ok=True)

            source_path: Path | None = None
            raw_doc = None
            relative_dir = ""

            if job.source_type == SourceKind.URL.value:
                direct_ocr_doc = self._direct_pdf_ocr_document(job)
                if direct_ocr_doc is not None:
                    raw_doc = direct_ocr_doc
                else:
                    importer_fn = self._resolve_importer_for_url(
                        job.source_uri, job.mime_hint
                    )
                    if importer_fn is None:
                        msg = "Unsupported URL ingestion source"
                        self.logger.warning(
                            "ingestion_job_failed",
                            data={
                                **log_context,
                                "event": "ingestion_job_failed",
                                "status": "failed",
                                "error_type": "UnsupportedSourceError",
                                "error": msg,
                                "issue": f"ingestion_job_failed:{job_id}",
                            },
                        )
                        self.mark_failed(job_id, msg)
                        return
                    url_cfg = (
                        ingestion_settings.get("url", {})
                        if isinstance(ingestion_settings, dict)
                        else {}
                    )
                    raw_doc = importer_fn(
                        job.source_uri,
                        timeout=url_cfg.get("read_timeout_seconds", 10),
                        connect_timeout=url_cfg.get("connect_timeout_seconds", 10),
                        strategy=url_cfg.get("fetch_strategy", "curl"),
                        max_bytes=url_cfg.get("max_response_bytes", 25 * 1024 * 1024),
                    )
                self.logger.info(
                    "ingestion_remote_classified",
                    data={
                        **log_context,
                        "event": "ingestion_remote_classified",
                        "status": "completed",
                        "source": sanitize_url_for_log(job.source_uri),
                        "detected_mime": raw_doc.mime,
                        "evidence": raw_doc.meta.get("classification_evidence"),
                        "effective_url": sanitize_url_for_log(
                            str(raw_doc.meta.get("effective_url") or job.source_uri)
                        ),
                    },
                )
            else:
                source_path = self._resolve_file_source(
                    source_uri=job.source_uri,
                    vault_root=vault_root,
                    import_root=import_root,
                    legacy_import_root=legacy_import_root,
                )
                if not source_path.exists():
                    raise FileNotFoundError(f"Source file not found: {source_path}")

                importer_fn = self._resolve_importer(source_path, job.mime_hint)
                if importer_fn is None:
                    msg = f"Unsupported file type for ingestion: {source_path.name}"
                    self.logger.warning(
                        "ingestion_job_failed",
                        data={
                            **log_context,
                            "event": "ingestion_job_failed",
                            "status": "failed",
                            "error_type": "UnsupportedSourceError",
                            "error": msg,
                            "issue": f"ingestion_job_failed:{job_id}",
                        },
                    )
                    self.mark_failed(job_id, msg)
                    return

                raw_doc = importer_fn(source_path)

            self.logger.info(
                "ingestion_source_resolved",
                data={
                    **log_context,
                    "event": "ingestion_source_resolved",
                    "status": "completed",
                    "source_kind": job.source_type,
                    "source": (
                        sanitize_url_for_log(job.source_uri)
                        if job.source_type == SourceKind.URL.value
                        else job.source_uri
                    ),
                    "source_disposition": (
                        "consume"
                        if bool((job.options or {}).get("consume_source"))
                        else "preserve"
                    ),
                    "detected_mime": raw_doc.mime,
                },
            )

            suffix = source_path.suffix.lower() if source_path else ""
            options: dict[str, Any] = (
                job.options if isinstance(job.options, dict) else {}
            )
            pdf_mode = (
                str(
                    options.get(
                        "pdf_mode",
                        ingestion_settings.get("pdf", {}).get(
                            "default_mode", "markdown"
                        ),
                    )
                )
                .strip()
                .lower()
                if isinstance(options, dict)
                else "markdown"
            )
            output_base_dir = self._resolve_output_base_dir(
                configured_value=options.get(
                    "output_path_pattern",
                    ingestion_settings.get("output_base_dir", "Imported/"),
                ),
                vault_root=vault_root,
            )
            if source_path:
                relative_dir = self._compute_relative_import_dir(
                    source_path=source_path,
                    import_root=import_root,
                )

            if raw_doc.mime == "application/pdf" and pdf_mode == "page_images":
                update_job_provenance(
                    job_id,
                    selected_strategy="pdf_page_images",
                    selected_provider="local",
                    selected_model=None,
                    strategy_attempts=["pdf_page_images"],
                    fallback_reason=None,
                )
                outputs = self._render_pdf_page_images(
                    raw_doc=raw_doc,
                    vault=vault,
                    source_path=source_path,
                    relative_dir=relative_dir,
                    base_output_dir=output_base_dir,
                    dpi=150,
                )
                update_job_outputs(job_id, outputs)
                self._cleanup_source_file_if_requested(
                    job=job,
                    source_path=source_path,
                    vault=vault,
                )
                self.mark_completed(job_id)
                self.logger.info(
                    "ingestion_job_completed",
                    data={
                        **log_context,
                        "event": "ingestion_job_completed",
                        "status": "completed",
                        "pdf_mode": "page_images",
                        "primary_output": outputs[0] if outputs else None,
                        "outputs_count": len(outputs),
                    },
                )
                return

            strategies = self._get_strategies(
                job,
                suffix,
                raw_doc.mime,
                ingestion_settings,
            )
            extractor_opts = (
                options.get("extractor_options", {})
                if isinstance(options, dict)
                else {}
            )
            self.logger.info(
                "ingestion_strategies_resolved",
                data={
                    **log_context,
                    "event": "ingestion_strategies_resolved",
                    "status": "selected",
                    "strategies": strategies,
                    "pdf_mode": pdf_mode,
                    "extractor_option_keys": sorted(extractor_opts.keys()),
                },
            )
            extracted, warnings, attempts = self._run_strategies(
                raw_doc,
                strategies,
                ingestion_settings,
                extractor_opts,
                log_context=log_context,
            )
            if extracted is None:
                msg = f"No extractor succeeded for {raw_doc.mime or 'unknown mime'}"
                update_job_provenance(
                    job_id,
                    selected_strategy=None,
                    selected_provider=None,
                    selected_model=None,
                    strategy_attempts=attempts,
                    fallback_reason=self._truncate_log_value(
                        "; ".join(warnings or []) or msg
                    ),
                )
                self.logger.warning(
                    "ingestion_job_failed",
                    data={
                        **log_context,
                        "event": "ingestion_job_failed",
                        "status": "failed",
                        "issue": f"ingestion_job_failed:{job_id}",
                        "reason": msg,
                        "error_type": "ExtractionUnavailableError",
                        "error": msg,
                        "strategies": strategies,
                        **self._strategy_warning_summary(warnings),
                    },
                )
                self.mark_failed(job_id, msg)
                return

            update_job_provenance(
                job_id,
                selected_strategy=extracted.strategy_id,
                selected_provider=str(extracted.meta.get("provider") or "local"),
                selected_model=(
                    str(extracted.meta["model"])
                    if extracted.meta.get("model")
                    else None
                ),
                strategy_attempts=attempts,
                fallback_reason=(
                    self._truncate_log_value("; ".join(warnings)) if warnings else None
                ),
            )

            render_options = RenderOptions(
                mode=RenderMode.FULL,
                store_original=False,
                title=raw_doc.suggested_title,
                vault=vault,
                source_filename=str(source_path) if source_path else job.source_uri,
                source_uri=job.source_uri,
                effective_source_uri=(
                    str(raw_doc.meta.get("effective_url"))
                    if raw_doc.meta.get("effective_url")
                    else None
                ),
                relative_dir=relative_dir,
                path_pattern=output_base_dir,
            )
            if warnings:
                extracted.meta.setdefault("warnings", []).extend(warnings)
            rendered = default_renderer(extracted, render_options)
            outputs = default_storage(rendered, render_options)

            update_job_outputs(job_id, outputs)
            self._cleanup_source_file_if_requested(
                job=job,
                source_path=source_path,
                vault=vault,
            )
            self.mark_completed(job_id)
            self.logger.info(
                "ingestion_job_completed",
                data={
                    **log_context,
                    "event": "ingestion_job_completed",
                    "status": "completed",
                    "selected_strategy": extracted.strategy_id,
                    "strategies": strategies,
                    **self._strategy_warning_summary(warnings),
                    "primary_output": outputs[0] if outputs else None,
                    "outputs_count": len(outputs),
                },
            )
        except Exception as exc:
            self.logger.error(
                "ingestion_job_failed",
                data={
                    **log_context,
                    "event": "ingestion_job_failed",
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": self._truncate_log_value(str(exc)),
                    "issue": f"ingestion_job_failed:{job_id}",
                },
            )
            if job.source_type == SourceKind.URL.value:
                try:
                    url_cfg = (
                        ingestion_settings.get("url", {})
                        if isinstance(ingestion_settings, dict)
                        else {}
                    )
                except Exception:
                    url_cfg = {}
                self.logger.set_sinks(["validation"]).error(
                    "URL ingestion failed",
                    data={
                        "event": "ingestion_url_failure_details",
                        "status": "failed",
                        "job_id": job_id,
                        "vault_name": job.vault,
                        "source_uri": sanitize_url_for_log(job.source_uri),
                        "source_type": job.source_type,
                        "fetch_strategy": url_cfg.get("fetch_strategy", "curl"),
                        "connect_timeout_seconds": url_cfg.get(
                            "connect_timeout_seconds"
                        ),
                        "read_timeout_seconds": url_cfg.get("read_timeout_seconds"),
                        "error_type": type(exc).__name__,
                        "error": self._truncate_log_value(str(exc)),
                    },
                )
            self.mark_failed(job_id, str(exc))
            raise

    @staticmethod
    def _direct_pdf_ocr_document(job: IngestionJob) -> RawDocument | None:
        """Build a URL-backed PDF document when OCR is the sole requested strategy."""
        options = job.options if isinstance(job.options, dict) else {}
        strategies = options.get("strategies") or options.get("pdf_strategies")
        if strategies != ["pdf_ocr"] or options.get("pdf_mode") == "page_images":
            return None
        parsed = urlsplit(job.source_uri)
        if Path(unquote(parsed.path)).suffix.lower() != ".pdf":
            return None
        resolve_public_url(job.source_uri)
        name = Path(unquote(parsed.path)).stem or "import"
        return RawDocument(
            source_uri=job.source_uri,
            kind=SourceKind.URL,
            mime="application/pdf",
            payload=b"",
            suggested_title=name,
            meta={
                "effective_url": job.source_uri,
                "classification_evidence": "url_suffix",
                "ocr_document_url": job.source_uri,
            },
        )

    def _resolve_file_source(
        self,
        *,
        source_uri: str,
        vault_root: Path,
        import_root: Path,
        legacy_import_root: Path,
    ) -> Path:
        raw_path = Path(source_uri)
        if raw_path.is_absolute():
            raise ValueError("Ingestion file source must be vault-relative")

        candidate = (vault_root / raw_path).resolve()
        try:
            candidate.relative_to(vault_root)
        except ValueError as exc:
            raise ValueError("Ingestion file source escapes the vault") from exc

        if candidate.exists():
            return candidate

        # Compatibility for queued jobs created before sources were persisted as
        # vault-relative paths.
        if raw_path.parent == Path("."):
            inbox_candidate = (import_root / raw_path).resolve()
            if inbox_candidate.exists():
                return inbox_candidate
            legacy_candidate = (legacy_import_root / raw_path).resolve()
            if legacy_candidate.exists():
                return legacy_candidate
        return candidate

    def _compute_relative_import_dir(self, source_path: Path, import_root: Path) -> str:
        try:
            source_parent = source_path.parent.resolve()
            import_root_resolved = import_root.resolve()
            if str(source_parent).startswith(str(import_root_resolved)):
                relative_dir = str(
                    source_parent.relative_to(import_root_resolved)
                ).strip("/")
                if relative_dir in ("", "."):
                    return ""
                return f"{relative_dir}/"
        except Exception:
            return ""
        return ""

    @staticmethod
    def _resolve_output_base_dir(*, configured_value: object, vault_root: Path) -> str:
        raw_value = str(
            "Imported/" if configured_value is None else configured_value
        ).strip()
        if raw_value in {"", "."}:
            return ""
        output_path = Path(raw_value)
        if output_path.is_absolute():
            raise ValueError("Ingestion output path must be vault-relative")
        resolved = (vault_root / output_path).resolve()
        try:
            relative = resolved.relative_to(vault_root)
        except ValueError as exc:
            raise ValueError("Ingestion output path escapes the vault") from exc
        return relative.as_posix()

    def _cleanup_source_file_if_requested(
        self,
        *,
        job: IngestionJob,
        source_path: Path | None,
        vault: str,
    ) -> None:
        if source_path is None or not bool((job.options or {}).get("consume_source")):
            return
        try:
            data_root = Path(get_data_root())
            vault_root = resolve_configured_vault_root(
                data_root=data_root, vault_name=vault
            )
            resolved_source = source_path.resolve()
            try:
                relative_source = resolved_source.relative_to(vault_root).as_posix()
            except ValueError as exc:
                raise RuntimeError(
                    "Consumed ingestion source resolved outside its vault"
                ) from exc
            if resolved_source.is_file():
                delete_vault_file(
                    vault_path=vault_root,
                    path=relative_source,
                    warn_without_task=False,
                )
                self.logger.info(
                    "ingestion_source_cleanup",
                    data={
                        "event": "ingestion_source_cleanup",
                        "status": "completed",
                        "job_id": job.id,
                        "vault_name": vault,
                        "source": relative_source,
                    },
                )
        except Exception as exc:
            self.logger.warning(
                "ingestion_source_cleanup",
                data={
                    "event": "ingestion_source_cleanup",
                    "status": "failed",
                    "job_id": job.id,
                    "vault_name": vault,
                    "source": job.source_uri,
                    "issue": f"ingestion_source_cleanup:{job.id}",
                    "error_type": type(exc).__name__,
                    "error": self._truncate_log_value(str(exc)),
                },
            )
            raise RuntimeError(
                f"Imported content was written, but source cleanup failed: {exc}"
            ) from exc

    def _render_pdf_page_images(
        self,
        *,
        raw_doc: RawDocument,
        vault: str,
        source_path: Path | None,
        relative_dir: str,
        base_output_dir: str,
        dpi: int,
    ) -> list[str]:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise RuntimeError(
                "PyMuPDF is required for PDF page image rendering"
            ) from exc

        source_filename = str(source_path) if source_path else raw_doc.source_uri
        paths = resolve_import_output_paths(
            path_pattern=base_output_dir,
            relative_dir=relative_dir,
            source_filename=source_filename,
            title=raw_doc.suggested_title,
        )

        asset_dir_rel = Path(paths.asset_dir)
        pages_dir_rel = asset_dir_rel / "pages"
        markdown_rel = Path(paths.markdown_path)

        data_root = Path(get_data_root())
        vault_root = data_root / vault

        payload = (
            raw_doc.payload
            if isinstance(raw_doc.payload, bytes | bytearray)
            else raw_doc.payload.encode("utf-8")
        )
        doc = fitz.open(stream=payload, filetype="pdf")
        zoom = max(1, int(dpi)) / 72.0
        matrix = fitz.Matrix(zoom, zoom)

        page_paths: list[str] = []
        for idx, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            filename = f"page_{idx:04d}.png"
            page_path = (pages_dir_rel / filename).as_posix()
            write_vault_file_bytes(
                vault_path=vault_root,
                path=page_path,
                content=pix.tobytes("png"),
                warn_without_task=False,
            )
            page_paths.append(page_path)

        source_hash = hashlib.sha256(payload).hexdigest()
        source_mtime = None
        if source_path is not None and source_path.exists():
            try:
                source_mtime = source_path.stat().st_mtime
            except Exception:
                source_mtime = None

        source_name = (
            source_path.name
            if source_path
            else (raw_doc.suggested_title or "import.pdf")
        )
        source_value = str(source_path) if source_path else raw_doc.source_uri
        frontmatter: dict[str, object] = {
            "source": source_value,
            "source_name": source_name,
            "mime": raw_doc.mime or "application/pdf",
            "sha256": source_hash,
            "import_mode": "page_images",
            "render_format": "png",
            "render_dpi": int(dpi),
            "page_count": len(page_paths),
        }
        if source_mtime is not None:
            frontmatter["source_mtime"] = source_mtime
        frontmatter_text = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
        ).strip()
        page_links = [
            f"![Page {idx}]({Path(page_path).relative_to(markdown_rel.parent).as_posix()})"
            for idx, page_path in enumerate(page_paths, start=1)
        ]
        write_vault_file(
            vault_path=vault_root,
            path=markdown_rel.as_posix(),
            content="\n".join(["---", frontmatter_text, "---", "", *page_links, ""]),
            warn_without_task=False,
        )

        return [markdown_rel.as_posix(), *page_paths]

    def _resolve_importer(
        self, source_path: Path, mime_hint: str | None
    ) -> Callable[..., RawDocument] | None:
        """
        Pick the first registered importer matching mime hint or file extension.
        """
        candidates = []
        if mime_hint:
            candidates.extend(importer_registry.get(mime_hint.lower()))

        suffix = source_path.suffix.lower()
        if suffix:
            candidates.extend(importer_registry.get(suffix))

        return candidates[0] if candidates else None

    def _resolve_importer_for_url(
        self, source_uri: str, mime_hint: str | None
    ) -> Callable[..., RawDocument] | None:
        """
        Pick importer for URLs based on scheme/mime.
        """
        candidates = []
        if mime_hint:
            candidates.extend(importer_registry.get(mime_hint.lower()))
        try:
            from urllib.parse import urlparse

            scheme = urlparse(source_uri).scheme.lower()
            if scheme:
                candidates.extend(importer_registry.get(f"scheme:{scheme}"))
        except Exception:
            pass
        candidates.extend(importer_registry.get("url"))
        return candidates[0] if candidates else None

    def _get_ingestion_settings(self) -> dict[str, Any]:
        """
        Map general settings entries to a simple ingestion settings dict.
        """
        general_settings = get_general_settings()

        def setting_value(key: str) -> Any:
            entry = general_settings.get(key)
            if entry is None:
                raise KeyError(key)
            return entry.value

        pdf_default_strategies: list[str] = []
        pdf_default_mode = "markdown"
        ocr_model = "mistral-ocr-latest"
        ocr_endpoint = "https://api.mistral.ai/v1/ocr"
        image_default_strategies: list[str] = []
        base_output_dir = "Imported/"
        url_read_timeout_seconds = 10
        url_connect_timeout_seconds = 10
        url_fetch_strategy = "curl"
        url_max_response_mb = 25
        try:
            pdf_default_strategies = list(
                setting_value("ingestion_pdf_default_strategies")
            )
        except Exception:
            pdf_default_strategies = []
        try:
            configured_pdf_mode = (
                str(setting_value("ingestion_pdf_default_mode")).strip().lower()
            )
            if configured_pdf_mode in {"markdown", "page_images"}:
                pdf_default_mode = configured_pdf_mode
        except Exception:
            pdf_default_mode = "markdown"
        try:
            ocr_model = str(setting_value("ingestion_ocr_model"))
        except Exception:
            try:
                ocr_model = str(setting_value("ingestion_pdf_ocr_model"))
            except Exception:
                ocr_model = "mistral-ocr-latest"
        try:
            ocr_endpoint = str(setting_value("ingestion_ocr_endpoint"))
        except Exception:
            try:
                ocr_endpoint = str(setting_value("ingestion_pdf_ocr_endpoint"))
            except Exception:
                ocr_endpoint = "https://api.mistral.ai/v1/ocr"
        try:
            image_default_strategies = list(
                setting_value("ingestion_image_default_strategies")
            )
        except Exception:
            image_default_strategies = []
        try:
            base_output_dir = str(setting_value("ingestion_output_path_pattern"))
        except Exception:
            base_output_dir = "Imported/"
        try:
            url_read_timeout_seconds = int(
                setting_value("ingestion_url_read_timeout_seconds")
            )
        except Exception:
            url_read_timeout_seconds = 10
        try:
            url_connect_timeout_seconds = int(
                setting_value("ingestion_url_connect_timeout_seconds")
            )
        except Exception:
            url_connect_timeout_seconds = 10
        try:
            url_fetch_strategy = (
                str(setting_value("ingestion_url_fetch_strategy")).strip().lower()
                or "curl"
            )
        except Exception:
            url_fetch_strategy = "curl"
        try:
            url_max_response_mb = int(setting_value("ingestion_url_max_response_mb"))
        except Exception:
            url_max_response_mb = 25

        return {
            "pdf": {
                "default_strategies": pdf_default_strategies,
                "default_mode": pdf_default_mode,
                "ocr_model": ocr_model,
                "ocr_endpoint": ocr_endpoint,
            },
            "image": {
                "default_strategies": image_default_strategies,
                "ocr_model": ocr_model,
                "ocr_endpoint": ocr_endpoint,
            },
            "output_base_dir": base_output_dir,
            "url": {
                "read_timeout_seconds": max(1, url_read_timeout_seconds),
                "connect_timeout_seconds": max(1, url_connect_timeout_seconds),
                "fetch_strategy": url_fetch_strategy,
                "max_response_bytes": max(1, url_max_response_mb) * 1024 * 1024,
            },
        }

    def _resolve_extractor(
        self, mime: str | None
    ) -> Callable[..., ExtractedDocument] | None:
        """
        Pick the first registered extractor for the given MIME type.
        """
        if not mime:
            return None
        candidates = extractor_registry.get(mime.lower())
        return candidates[0] if candidates else None

    def _resolve_extractor_by_strategy(
        self, strategy_id: str
    ) -> Callable[..., ExtractedDocument] | None:
        """
        Pick extractor registered for a specific strategy id, falling back to MIME when strategy matches known mime.
        """
        candidates = extractor_registry.get(f"strategy:{strategy_id}")
        if candidates:
            return cast(Callable[..., ExtractedDocument], candidates[0])
        # Allow strategy ids that equal a MIME type
        candidates = extractor_registry.get(strategy_id.lower())
        return (
            cast(Callable[..., ExtractedDocument], candidates[0])
            if candidates
            else None
        )

    def _get_strategies(
        self,
        job: IngestionJob,
        suffix: str,
        mime: str | None,
        ingestion_settings: dict[str, Any],
    ) -> list[str]:
        """
        Determine strategy order for a job from options or mime defaults.
        """
        opts: dict[str, Any] = job.options or {}
        strategies = opts.get("strategies")
        if isinstance(strategies, list) and strategies:
            return [str(s) for s in strategies]

        # Defaults from settings
        pdf_cfg = (
            ingestion_settings.get("pdf", {})
            if isinstance(ingestion_settings, dict)
            else {}
        )
        image_cfg = (
            ingestion_settings.get("image", {})
            if isinstance(ingestion_settings, dict)
            else {}
        )
        normalized_mime = (mime or "").strip().lower()
        if normalized_mime == "application/pdf" or suffix == ".pdf":
            pdf_override = opts.get("pdf_strategies")
            if isinstance(pdf_override, list) and pdf_override:
                return [str(strategy) for strategy in pdf_override]
            cfg_strategies = pdf_cfg.get("default_strategies") or []
            default_strats = (
                [str(s) for s in cfg_strategies]
                if cfg_strategies
                else ["pdf_ocr", "pdf_text"]
            )
            return default_strats
        if normalized_mime.startswith("image/") or suffix in {
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".tif",
            ".tiff",
        }:
            cfg_strategies = image_cfg.get("default_strategies") or []
            default_strats = (
                [str(s) for s in cfg_strategies] if cfg_strategies else ["image_ocr"]
            )
            return default_strats
        if normalized_mime == "text/html":
            return ["html_markdownify"]
        return []

    def _run_strategies(
        self,
        raw_doc: RawDocument,
        strategies: list[str],
        ingestion_settings: dict[str, Any],
        options: dict[str, Any] | None = None,
        *,
        log_context: dict[str, Any] | None = None,
    ) -> tuple[ExtractedDocument | None, list[str] | None, list[str]]:
        """
        Try extractors in order; return first non-empty result.
        """
        warnings: list[str] = []
        attempts: list[str] = []
        extractor_options = options or {}
        base_log_context = log_context or {}
        for strat in strategies:
            attempts.append(strat)
            secret_name = self._STRATEGY_SECRET_REQUIREMENTS.get(strat)
            if secret_name and not secret_has_value(secret_name):
                warning = f"{strat}:missing_secret:{secret_name}"
                warnings.append(warning)
                self.logger.info(
                    "ingestion_strategy_skipped",
                    data={
                        **base_log_context,
                        "event": "ingestion_strategy_skipped",
                        "status": "skipped",
                        "strategy": strat,
                        "reason": "missing_secret",
                        "secret_name": secret_name,
                    },
                )
                continue

            extractor_fn = self._resolve_extractor_by_strategy(strat)
            if extractor_fn is None:
                warning = f"{strat}:missing"
                warnings.append(warning)
                self.logger.info(
                    "ingestion_strategy_skipped",
                    data={
                        **base_log_context,
                        "event": "ingestion_strategy_skipped",
                        "status": "skipped",
                        "strategy": strat,
                        "reason": "missing_extractor",
                    },
                )
                continue
            try:
                result = (
                    extractor_fn(raw_doc, extractor_options)
                    if extractor_fn.__code__.co_argcount > 1
                    else extractor_fn(raw_doc)
                )
            except Exception as exc:
                warning = f"{strat}:error:{exc}"
                warnings.append(warning)
                self.logger.set_sinks(["validation"]).info(
                    "ingestion_strategy_failed",
                    data={
                        **base_log_context,
                        "event": "ingestion_strategy_failed",
                        "status": "failed",
                        "strategy": strat,
                        "error_type": type(exc).__name__,
                        "error": self._truncate_log_value(str(exc)),
                    },
                )
                continue

            text = (result.plain_text or "").strip()
            if text:
                if strat != result.strategy_id:
                    result.strategy_id = strat
                self.logger.info(
                    "ingestion_strategy_selected",
                    data={
                        **base_log_context,
                        "event": "ingestion_strategy_selected",
                        "status": "selected",
                        "strategy": strat,
                        **self._strategy_warning_summary(warnings),
                    },
                )
                return result, warnings if warnings else None, attempts
            warning = f"{strat}:empty"
            warnings.append(warning)
            self.logger.info(
                "ingestion_strategy_empty",
                data={
                    **base_log_context,
                    "event": "ingestion_strategy_empty",
                    "status": "empty",
                    "strategy": strat,
                },
            )

        return None, warnings if warnings else None, attempts

    def _job_log_context(self, job: IngestionJob) -> dict[str, Any]:
        options: dict[str, Any] = job.options if isinstance(job.options, dict) else {}
        source_uri = (
            sanitize_url_for_log(job.source_uri)
            if job.source_type == SourceKind.URL.value
            else job.source_uri
        )
        return {
            "job_id": job.id,
            "vault": job.vault,
            "vault_name": job.vault,
            "source_uri": source_uri,
            "source_type": job.source_type,
            "mime_hint": job.mime_hint,
            "option_keys": sorted(options.keys()),
        }

    @staticmethod
    def _truncate_log_value(value: str, limit: int = 500) -> str:
        if len(value) <= limit:
            return value
        return f"{value[:limit]}..."

    @staticmethod
    def _strategy_warning_summary(
        warnings: list[str] | None,
    ) -> dict[str, object]:
        """Return bounded reason codes for retained ingestion activity."""
        values = warnings or []
        reasons: set[str] = set()
        for warning in values:
            parts = warning.split(":", 2)
            reasons.add(parts[1] if len(parts) > 1 else "unknown")
        ordered_reasons = sorted(reasons)
        return {
            "warning_count": len(values),
            "warning_reasons": ordered_reasons[:20],
            "warning_reasons_truncated": len(ordered_reasons) > 20,
        }

    def _load_builtin_handlers(self) -> None:
        """
        Import built-in source/extractor modules so they register themselves.
        """
        # Imports are intentional for side effects (registry registration).
        import core.ingestion.sources.image  # noqa: F401
        import core.ingestion.sources.pdf  # noqa: F401
        import core.ingestion.sources.web  # noqa: F401
        import core.ingestion.strategies.html_raw  # noqa: F401
        import core.ingestion.strategies.image_ocr  # noqa: F401
        import core.ingestion.strategies.pdf_ocr  # noqa: F401
        import core.ingestion.strategies.pdf_text  # noqa: F401
