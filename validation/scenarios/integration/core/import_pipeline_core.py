"""
Validation scenario for the ingestion pipeline (file import flow).

Creates a small PDF in AssistantMD/Import, runs the import scan via API,
and asserts the rendered markdown output exists while the source file is removed.
"""

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.identity import SYSTEM_AUTHORITY
from core.ingestion.models import SourceKind
from core.ingestion.task_execution import process_ingestion_job_in_task
from core.runtime.execution_tasks import ExecutionTaskSource
from core.runtime.state import get_runtime_context
from validation.core.base_scenario import BaseScenario


class ImportPipelineScenario(BaseScenario):
    """Validate importing a PDF from AssistantMD/Import."""

    async def test_scenario(self):
        vault = self.create_vault("ImportPipelineVault")

        # Create a tiny PDF in the import folder
        pdf_bytes = self.make_pdf("Import validation\nLine two")
        import_path = vault / "AssistantMD" / "Import" / "sample.pdf"
        import_path.parent.mkdir(parents=True, exist_ok=True)
        import_path.write_bytes(pdf_bytes)

        await self.start_system()

        # Trigger import scan (processes immediately by default)
        with patch("core.ingestion.service.secret_has_value", return_value=False):
            response = self.call_api(
                "/api/import/scan",
                method="POST",
                data={"vault": vault.name, "queue_only": False},
            )
        assert response.status_code == 200, "Import scan should succeed"
        payload = response.json()
        jobs = payload.get("jobs_created") or []
        assert len(jobs) == 1, "One job should be created for the PDF"
        job = jobs[0]
        assert job.get("status") == "completed", "Job should complete inline"
        assert job.get("selected_strategy") == "pdf_text"
        assert job.get("selected_provider") == "local"
        assert job.get("strategy_attempts") == ["pdf_ocr", "pdf_text"]
        assert job.get("fallback_reason") == "pdf_ocr:missing_secret:MISTRAL_API_KEY"
        outputs = job.get("outputs") or []
        assert len(outputs) > 0, "Import scan should return at least one output path"
        sample_rel_path = outputs[0]
        assert sample_rel_path.endswith(".md"), "Import output should be markdown"
        assert sample_rel_path.startswith(
            "Imported/"
        ), "Import output should be under Imported/"

        # Source file should be removed after successful import
        assert not import_path.exists(), "Source file should be cleaned up"

        # Validate the rendered markdown exists and contains the extracted text
        sample_path = vault / sample_rel_path
        assert sample_path.exists(), f"Expected {sample_rel_path} to be created"
        sample_content = sample_path.read_text()
        assert "Import validation" in sample_content
        assert "mime: application/pdf" in sample_content

        system_activity = self.call_api("/api/system/activity-log?limit=200")
        assert system_activity.status_code == 200, "System Activity should respond"
        ingestion_events = [
            entry.get("data") or {}
            for entry in system_activity.json().get("entries", [])
            if (entry.get("data") or {}).get("job_id") == job.get("id")
        ]
        observed_lifecycle = {
            (entry.get("event"), entry.get("status")) for entry in ingestion_events
        }
        assert (
            "ingestion_job_started",
            "started",
        ) in observed_lifecycle, "Import start should be visible in System Activity"
        assert (
            "ingestion_job_completed",
            "completed",
        ) in observed_lifecycle, (
            "Import completion should be visible in System Activity"
        )
        assert all(
            entry.get("vault_name") == vault.name for entry in ingestion_events
        ), "Import activity should retain the searchable vault identity"
        assert "Import validation" not in str(
            ingestion_events
        ), "Import activity must not retain document content"
        completed_activity = next(
            entry
            for entry in ingestion_events
            if entry.get("event") == "ingestion_job_completed"
        )
        assert completed_activity.get("warning_count") == 1
        assert completed_activity.get("warning_reasons") == ["missing_secret"]
        assert (
            "warnings" not in completed_activity
        ), "System Activity should retain warning reason codes, not raw warning text"

        vault_id = self._vault_id(vault.name)
        assert self._manifest_row(
            vault_id, sample_rel_path, deleted=False
        ), "Imported file should be tracked in vault state"
        assert self._manifest_row(
            vault_id,
            "AssistantMD/Import/sample.pdf",
            deleted=True,
        ), "Cleaned-up source file should be marked deleted in vault state"

        activity_response = self.call_api(f"/api/vaults/{vault.name}/activity")
        assert (
            activity_response.status_code == 200
        ), "Vault Activity mutation API should respond"
        groups = activity_response.json().get("groups") or []
        ingestion_group = next(
            (
                group
                for group in groups
                if group.get("activity_kind") == "ingestion"
                and group.get("task_source") == "api"
            ),
            None,
        )
        assert (
            ingestion_group is not None
        ), "Import should be visible as API ingestion Vault Activity"
        mutations = ingestion_group.get("mutations") or []
        observed = {
            (mutation.get("operation"), mutation.get("path")) for mutation in mutations
        }
        assert (
            "write",
            sample_rel_path,
        ) in observed, "Imported file write should be recorded"
        assert (
            "delete",
            "AssistantMD/Import/sample.pdf",
        ) in observed, "Source cleanup delete should be recorded"
        assert all(
            mutation.get("event_sequence") is not None for mutation in mutations
        ), "Import mutations should link to vault-state events"

        # A source selected elsewhere in the vault is preserved after import.
        research_path = vault / "Research" / "preserved.pdf"
        research_path.parent.mkdir(parents=True, exist_ok=True)
        research_path.write_bytes(self.make_pdf("Preserved source validation"))
        runtime = get_runtime_context()
        preserved_job = runtime.ingestion.enqueue_job(
            source_uri="Research/preserved.pdf",
            vault=vault.name,
            source_type=SourceKind.FILE.value,
            mime_hint=None,
            options={"consume_source": False},
        )
        assert runtime.ingestion.claim_job(
            preserved_job.id
        ), "Preserved-source job should be claimed before processing"
        with patch("core.ingestion.service.secret_has_value", return_value=False):
            await process_ingestion_job_in_task(
                task_coordinator=runtime.task_coordinator,
                process_job_fn=runtime.ingestion.process_job,
                job_id=preserved_job.id,
                vault=vault.name,
                source=ExecutionTaskSource.API,
                authority=SYSTEM_AUTHORITY,
            )
        preserved_result = runtime.ingestion.get_job(preserved_job.id)
        assert preserved_result is not None
        assert preserved_result.status == "completed"
        assert research_path.exists(), "Non-inbox source must be preserved"
        preserved_outputs = preserved_result.outputs or []
        assert len(preserved_outputs) == 1
        preserved_output = vault / preserved_outputs[0]
        assert preserved_output.exists()
        assert "Preserved source validation" in preserved_output.read_text()

        cleanup_jobs = []
        with patch(
            "core.ingestion.service.delete_vault_file",
            side_effect=RuntimeError("cleanup sentinel"),
        ):
            for suffix in ("one", "two"):
                cleanup_source = (
                    vault / "AssistantMD" / "Import" / f"cleanup-{suffix}.pdf"
                )
                cleanup_source.write_bytes(self.make_pdf(f"cleanup {suffix}"))
                cleanup_job = runtime.ingestion.enqueue_job(
                    source_uri=f"AssistantMD/Import/cleanup-{suffix}.pdf",
                    vault=vault.name,
                    source_type=SourceKind.FILE.value,
                    mime_hint="application/pdf",
                    options={"consume_source": True},
                )
                cleanup_jobs.append(cleanup_job)
                try:
                    runtime.ingestion._cleanup_source_file_if_requested(  # noqa: SLF001
                        job=cleanup_job,
                        source_path=cleanup_source,
                        vault=vault.name,
                    )
                except RuntimeError:
                    pass
                else:
                    raise AssertionError(
                        "Injected source cleanup failure should surface"
                    )
        cleanup_activity = self.call_api(
            "/api/system/activity-log?limit=100&tag=ingestion&search=cleanup sentinel"
        )
        assert cleanup_activity.status_code == 200
        cleanup_issues = {
            (entry.get("data") or {}).get("issue")
            for entry in cleanup_activity.json().get("entries", [])
            if (entry.get("data") or {}).get("event") == "ingestion_source_cleanup"
        }
        assert cleanup_issues == {
            f"ingestion_source_cleanup:{job.id}" for job in cleanup_jobs
        }, "Distinct ingestion cleanup failures should survive warning deduplication"

        await self.stop_system()
        self.teardown_scenario()

    def _vault_id(self, vault_name: str) -> str:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT vault_id FROM vaults WHERE current_name = ?",
                (vault_name,),
            ).fetchone()
            assert row is not None, f"Expected vault-state row for {vault_name}"
            return row[0]
        finally:
            conn.close()

    def _manifest_row(self, vault_id: str, path: str, *, deleted: bool) -> bool:
        db_path = self._get_system_controller()._system_root / "vault_state.db"
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                """
                SELECT deleted_at
                FROM vault_files
                WHERE vault_id = ? AND path = ?
                """,
                (vault_id, path),
            ).fetchone()
            if row is None:
                return False
            return (row[0] is not None) is deleted
        finally:
            conn.close()
