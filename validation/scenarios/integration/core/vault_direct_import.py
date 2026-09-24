"""Validate direct Vault Explorer source imports through the shared pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.ingestion.jobs import IngestionJobCreate, count_jobs, create_jobs
from core.runtime.state import get_runtime_context
from validation.core.base_scenario import BaseScenario


class VaultDirectImportScenario(BaseScenario):
    """Prove explicit destinations, queueing, preservation, and collisions."""

    async def test_scenario(self) -> None:
        vault = self.create_vault("VaultDirectImportVault")
        source = vault / "Uploads" / "source.pdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(self.make_pdf("Direct import validation"))
        unsupported = vault / "Uploads" / "notes.txt"
        unsupported.write_text("unsupported", encoding="utf-8")

        await self.start_system()
        with patch("core.ingestion.service.secret_has_value", return_value=False):
            queued = self.call_api(
                "/api/import/sources",
                method="POST",
                data={
                    "vault": vault.name,
                    "sources": ["Uploads/source.pdf"],
                    "destination": "Library",
                    "queue_only": True,
                    "strategies": ["pdf_text"],
                },
            )
            self.soft_assert_equal(
                queued.status_code,
                200,
                "Direct source import should accept a supported vault file",
            )
            queued_jobs = queued.json().get("jobs_created") or []
            self.soft_assert_equal(
                queued_jobs[0].get("status") if queued_jobs else None,
                "queued",
                "Queue-only direct import should return durable queued state",
            )
            queued_job_id = queued_jobs[0].get("id") if queued_jobs else None
            self.soft_assert_equal(
                queued_jobs[0].get("request_options") if queued_jobs else None,
                {
                    "destination": "Library",
                    "strategies": ["pdf_text"],
                    "pdf_strategies": None,
                    "pdf_mode": "markdown",
                    "capture_ocr_images": False,
                    "clean_html": True,
                    "include_ocr_blocks": None,
                    "ocr_table_format": None,
                    "extract_ocr_header": None,
                    "extract_ocr_footer": None,
                    "ocr_confidence": None,
                },
                "Job responses should expose the effective user-facing options",
            )
            self.soft_assert_equal(
                queued_jobs[0].get("can_resubmit") if queued_jobs else None,
                False,
                "Queued vault-file jobs should not allow duplicate resubmission",
            )
            queued_job = get_runtime_context().ingestion.get_job(queued_job_id)
            self.soft_assert_equal(
                (
                    (queued_job.options or {}).get("output_path_pattern")
                    if queued_job
                    else None
                ),
                "Library",
                "The submitted job should snapshot the explicit destination",
            )
            self.soft_assert_equal(
                (
                    (queued_job.options or {}).get("consume_source")
                    if queued_job
                    else None
                ),
                False,
                "Direct import should preserve its vault source",
            )
            activity = self.call_api(
                "/api/system/activity-log?limit=100&tag=api-services"
            )
            submitted_events = [
                entry.get("data") or {}
                for entry in activity.json().get("entries", [])
                if (entry.get("data") or {}).get("event") == "ingestion_jobs_submitted"
            ]
            self.soft_assert(
                any(
                    event.get("job_ids") == [queued_job_id]
                    and event.get("vault_name") == vault.name
                    and event.get("status") == "queued"
                    for event in submitted_events
                ),
                "Queue-only Explorer imports should emit searchable System Activity",
            )

            immediate = self.call_api(
                "/api/import/sources",
                method="POST",
                data={
                    "vault": vault.name,
                    "sources": ["Uploads/source.pdf"],
                    "destination": "",
                    "strategies": ["pdf_text"],
                },
            )
            immediate_jobs = immediate.json().get("jobs_created") or []
            self.soft_assert_equal(
                immediate_jobs[0].get("status") if immediate_jobs else None,
                "queued",
                "Immediate direct import should promptly acknowledge durable queued state",
            )
            immediate_job_id = immediate_jobs[0].get("id") if immediate_jobs else None
            immediate_status = self.call_api(f"/api/import/jobs/{immediate_job_id}")
            self.soft_assert_equal(
                immediate_status.json().get("status"),
                "completed",
                "Immediate direct import should continue processing after acknowledgement",
            )
            self.soft_assert_equal(
                immediate_status.json().get("outputs"),
                ["source.md"],
                "An explicit root destination should write at the vault root",
            )
            self.soft_assert_equal(
                immediate_status.json().get("can_resubmit"),
                True,
                "Terminal direct-import jobs should support Explorer resubmission",
            )
            self.soft_assert(
                source.exists(),
                "Successful direct import should preserve the source file",
            )
            lifecycle_activity = self.call_api(
                "/api/system/activity-log?limit=200&tag=ingestion"
            )
            immediate_events = [
                entry.get("data") or {}
                for entry in lifecycle_activity.json().get("entries", [])
                if (entry.get("data") or {}).get("job_id") == immediate_job_id
            ]
            observed_lifecycle = {
                (event.get("event"), event.get("status")) for event in immediate_events
            }
            self.soft_assert(
                {
                    ("ingestion_job_started", "started"),
                    ("ingestion_strategies_resolved", "selected"),
                    ("ingestion_job_completed", "completed"),
                }.issubset(observed_lifecycle),
                "Direct imports should expose a correlated start, decision, and completion lifecycle",
            )
            self.soft_assert(
                all(
                    event.get("vault_name") == vault.name for event in immediate_events
                ),
                "Direct-import lifecycle events should retain the searchable vault identity",
            )
            self.soft_assert(
                "Direct import validation" not in str(immediate_events),
                "Direct-import activity should not retain imported document content",
            )

            collision = self.call_api(
                "/api/import/sources",
                method="POST",
                data={
                    "vault": vault.name,
                    "sources": ["Uploads/source.pdf"],
                    "destination": "",
                    "strategies": ["pdf_text"],
                },
            )
            collision_jobs = collision.json().get("jobs_created") or []
            collision_job_id = collision_jobs[0].get("id") if collision_jobs else None
            collision_status = self.call_api(f"/api/import/jobs/{collision_job_id}")
            self.soft_assert_equal(
                collision_status.json().get("outputs"),
                ["source_1.md"],
                "Direct import collisions should use the existing numbered-copy policy",
            )

            saved_default = self.call_api(
                "/api/system/settings/general/ingestion_pdf_default_mode",
                method="PUT",
                data={"value": "page_images"},
            )
            self.soft_assert_equal(
                saved_default.status_code,
                200,
                "The Dashboard PDF output default should be persisted",
            )
            defaulted = self.call_api(
                "/api/import/sources",
                method="POST",
                data={
                    "vault": vault.name,
                    "sources": ["Uploads/source.pdf"],
                    "destination": "PageDefault",
                },
            )
            defaulted_jobs = defaulted.json().get("jobs_created") or []
            defaulted_job_id = defaulted_jobs[0].get("id") if defaulted_jobs else None
            defaulted_status = self.call_api(f"/api/import/jobs/{defaulted_job_id}")
            defaulted_outputs = defaulted_status.json().get("outputs") or []
            self.soft_assert(
                "PageDefault/source.md" in defaulted_outputs
                and any(
                    path.endswith("/pages/page_0001.png") for path in defaulted_outputs
                ),
                "An import without an override should use the persisted PDF mode default",
            )

            jobs_before_invalid = count_jobs()
            invalid = self.call_api(
                "/api/import/sources",
                method="POST",
                data={
                    "vault": vault.name,
                    "sources": ["Uploads/source.pdf", "Uploads/notes.txt"],
                    "destination": "Library",
                    "queue_only": True,
                },
            )
            self.soft_assert_equal(
                invalid.status_code,
                400,
                "A mixed batch with an unsupported source should fail preflight",
            )
            self.soft_assert_equal(
                count_jobs(),
                jobs_before_invalid,
                "A rejected direct-import batch should enqueue no jobs",
            )

            jobs_before_authority_failure = count_jobs()
            with patch(
                "api.endpoints.require_current_execution_authority",
                side_effect=RuntimeError("missing execution authority"),
            ):
                missing_authority = self.call_api(
                    "/api/import/sources",
                    method="POST",
                    data={
                        "vault": vault.name,
                        "sources": ["Uploads/source.pdf"],
                        "destination": "Library",
                    },
                )
            self.soft_assert_equal(
                missing_authority.status_code,
                500,
                "Immediate import should reject missing execution authority",
            )
            self.soft_assert_equal(
                count_jobs(),
                jobs_before_authority_failure,
                "Authority failure should occur before durable job creation",
            )

            jobs_before_repository_failure = count_jobs()
            batch_failed = False
            try:
                create_jobs(
                    [
                        IngestionJobCreate(
                            source_uri="valid-source.pdf",
                            vault=vault.name,
                            source_type="file",
                            mime_hint=None,
                            options={},
                        ),
                        IngestionJobCreate(
                            source_uri=None,  # type: ignore[arg-type]
                            vault=vault.name,
                            source_type="file",
                            mime_hint=None,
                            options={},
                        ),
                    ]
                )
            except RuntimeError:
                batch_failed = True
            self.soft_assert(
                batch_failed,
                "A repository failure should reject the ingestion job batch",
            )
            self.soft_assert_equal(
                count_jobs(),
                jobs_before_repository_failure,
                "A failed repository batch should commit no partial jobs",
            )

            legacy_job = get_runtime_context().ingestion.enqueue_job(
                source_uri="https://example.test/legacy",
                vault=vault.name,
                source_type="url",
                mime_hint=None,
                options={"consume_source": False},
            )
            legacy_info = self.call_api(f"/api/import/jobs/{legacy_job.id}")
            self.soft_assert_equal(
                (legacy_info.json().get("request_options") or {}).get("destination"),
                "Imported/",
                "Legacy jobs without a snapshot should not resubmit to vault root",
            )

            metadata = self.call_api("/api/metadata")
            file_import = (
                metadata.json().get("ingestion_capabilities", {}).get("file_import", {})
            )
            self.soft_assert_equal(
                file_import.get("features"),
                [".jpeg", ".jpg", ".pdf", ".png", ".tif", ".tiff", ".webp"],
                "Metadata should publish registry-owned file import extensions",
            )
