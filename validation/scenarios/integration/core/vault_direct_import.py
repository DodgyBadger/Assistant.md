"""Validate direct Vault Explorer source imports through the shared pipeline."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.ingestion.jobs import count_jobs
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
                "completed",
                "Immediate direct import should report terminal job state",
            )
            self.soft_assert_equal(
                immediate_jobs[0].get("outputs") if immediate_jobs else None,
                ["source.md"],
                "An explicit root destination should write at the vault root",
            )
            self.soft_assert(
                source.exists(),
                "Successful direct import should preserve the source file",
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
            self.soft_assert_equal(
                collision_jobs[0].get("outputs") if collision_jobs else None,
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
            defaulted_outputs = (
                defaulted_jobs[0].get("outputs") if defaulted_jobs else []
            )
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
