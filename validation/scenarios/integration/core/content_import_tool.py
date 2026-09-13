"""Validate the shared chat/Monty content import contract."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from pydantic_ai.messages import ToolReturn

from core.authoring.shared.tool_binding import resolve_tool_binding
from core.identity import LOCAL_USER_PRINCIPAL_ID
from core.ingestion.jobs import claim_queued_job, count_jobs, fail_processing_jobs
from core.runtime.state import get_runtime_context
from core.tools.content_import import ContentImport
from core.web.models import WebFetchResult
from validation.core.base_scenario import BaseScenario, with_local_user_authority


class ContentImportToolScenario(BaseScenario):
    """Prove singular/batch submission, status, preservation, and URL PDF routing."""

    @with_local_user_authority
    async def test_scenario(self) -> None:
        vault = self.create_vault("ContentImportToolVault")
        local_pdf = vault / "Research" / "local.pdf"
        local_pdf.parent.mkdir(parents=True, exist_ok=True)
        local_pdf.write_bytes(self.make_pdf("Local content import validation"))
        second_pdf = vault / "Research" / "second.pdf"
        second_pdf.write_bytes(self.make_pdf("Second content import validation"))

        await self.start_system()
        ocr_unavailable = patch(
            "core.ingestion.service.secret_has_value",
            return_value=False,
        )
        ocr_unavailable.start()
        try:
            binding = resolve_tool_binding(["content_import"], vault_path=str(vault))
            self.soft_assert_equal(
                binding.tool_names(),
                ["content_import"],
                "content_import should bind for Monty direct tool calls",
            )

            tool = ContentImport.get_tool(vault_path=str(vault))
            submit = await tool.function(
                operation="submit",
                sources="Research/local.pdf",
                options={"destination": "Research/Library"},
            )
            self.soft_assert(
                isinstance(submit, ToolReturn),
                "submit should return a structured ToolReturn",
            )
            submit_items = (submit.metadata or {}).get("items") or []
            self.soft_assert_equal(len(submit_items), 1, "submit should create one job")
            local_job_id = submit_items[0].get("job_id") if submit_items else None
            submit_payload = json.loads(str(submit.return_value))
            self.soft_assert_equal(
                submit_payload.get("items"),
                submit_items,
                "submit should expose job ids and records in the agent-visible result",
            )
            self.soft_assert_equal(
                submit_items[0].get("status") if submit_items else None,
                "completed",
                "submit should process immediately by default",
            )

            status = await tool.function(operation="status", job_ids=local_job_id)
            status_items = (status.metadata or {}).get("items") or []
            status_payload = json.loads(str(status.return_value))
            self.soft_assert_equal(
                status_payload.get("items"),
                status_items,
                "status should expose job records in the agent-visible result",
            )
            self.soft_assert_equal(
                status_items[0].get("status") if status_items else None,
                "completed",
                "status should expose terminal ingestion state",
            )
            self.soft_assert_equal(
                status_items[0].get("selected_strategy") if status_items else None,
                "pdf_text",
                "status should expose the selected extraction strategy",
            )
            self.soft_assert_equal(
                status_items[0].get("selected_provider") if status_items else None,
                "local",
                "status should expose the selected extraction provider",
            )
            self.soft_assert_equal(
                status_items[0].get("strategy_attempts") if status_items else None,
                ["pdf_ocr", "pdf_text"],
                "status should expose extraction attempts",
            )
            self.soft_assert_equal(
                status_items[0].get("fallback_reason") if status_items else None,
                "pdf_ocr:missing_secret:MISTRAL_API_KEY",
                "status should explain why OCR fell back to local extraction",
            )
            local_outputs = (
                status_items[0].get("outputs") if status_items else []
            ) or []
            self.soft_assert(
                local_outputs == ["Research/Library/local.md"],
                "Import Markdown should land directly in the per-job destination",
            )
            self.soft_assert(
                local_pdf.exists(),
                "content_import must preserve a vault-relative source",
            )

            ingestion_tasks = await get_runtime_context().task_coordinator.list_tasks(
                kind="ingestion"
            )
            local_task = next(
                (
                    task
                    for task in ingestion_tasks
                    if task.metadata.get("job_id") == local_job_id
                ),
                None,
            )
            self.soft_assert_equal(
                local_task.source if local_task else None,
                "tool",
                "Immediate content import should be attributed to the tool",
            )
            self.soft_assert_equal(
                local_task.principal_id if local_task else None,
                LOCAL_USER_PRINCIPAL_ID,
                "Immediate content import should retain the current principal",
            )

            batch_submit = await tool.function(
                operation="submit",
                sources=["Research/local.pdf", "Research/second.pdf"],
                options={"destination": "Research/Batch"},
            )
            batch_items = (batch_submit.metadata or {}).get("items") or []
            self.soft_assert_equal(
                [item.get("status") for item in batch_items],
                ["completed", "completed"],
                "Immediate batch submission should return terminal results",
            )

            remote_url = "https://example.org/reports/remote.pdf"
            remote_pdf = self.make_pdf("Remote PDF content import validation")
            fetch_result = WebFetchResult(
                source_url=remote_url,
                effective_url=remote_url,
                status_code=200,
                headers={"content-type": "application/octet-stream"},
                body=remote_pdf,
                remote_ip="203.0.113.10",
            )
            with patch(
                "core.ingestion.sources.web.fetch_url_with_curl",
                return_value=fetch_result,
            ):
                remote_submit = await tool.function(
                    operation="submit",
                    sources=[remote_url],
                )
            remote_items = (remote_submit.metadata or {}).get("items") or []
            remote_job_id = remote_items[0].get("job_id") if remote_items else None

            remote_status = await tool.function(
                operation="status",
                job_ids=[remote_job_id],
            )
            remote_status_items = (remote_status.metadata or {}).get("items") or []
            self.soft_assert_equal(
                remote_status_items[0].get("status") if remote_status_items else None,
                "completed",
                "PDF response bytes should route through PDF ingestion",
            )
            remote_outputs = (
                remote_status_items[0].get("outputs") if remote_status_items else []
            ) or []
            self.soft_assert_equal(
                len(remote_outputs), 1, "Remote PDF should create one markdown output"
            )
            if remote_outputs:
                self.soft_assert_equal(
                    remote_outputs[0],
                    "Imported/remote.md",
                    "Import Markdown should land directly in the default destination",
                )
                remote_content = (vault / remote_outputs[0]).read_text(encoding="utf-8")
                self.soft_assert(
                    "Remote PDF content import validation" in remote_content,
                    "Remote PDF output should contain extracted text",
                )
                self.soft_assert(
                    f"source: {remote_url}" in remote_content,
                    "Remote output should preserve requested URL provenance",
                )

            extensionless_url = "https://example.org/download?id=report"
            extensionless_fetch = WebFetchResult(
                source_url=extensionless_url,
                effective_url=extensionless_url,
                status_code=200,
                headers={"content-type": "application/pdf"},
                body=self.make_pdf("Extensionless PDF strategy validation"),
                remote_ip="203.0.113.10",
            )
            with patch(
                "core.ingestion.sources.web.fetch_url_with_curl",
                return_value=extensionless_fetch,
            ):
                extensionless_response = self.call_api(
                    "/api/import/url",
                    method="POST",
                    data={
                        "vault": vault.name,
                        "url": extensionless_url,
                        "pdf_strategies": ["pdf_text"],
                    },
                )
            self.soft_assert_equal(
                extensionless_response.status_code,
                200,
                "Extensionless PDF URLs should accept PDF-specific strategy intent",
            )
            self.soft_assert_equal(
                extensionless_response.json().get("selected_strategy"),
                "pdf_text",
                "PDF-specific strategy intent should apply after classification",
            )

            cancel_submit = await tool.function(
                operation="submit",
                sources="Research/local.pdf",
                queue_only=True,
            )
            cancel_items = (cancel_submit.metadata or {}).get("items") or []
            cancel_job_id = cancel_items[0].get("job_id") if cancel_items else None
            self.soft_assert_equal(
                cancel_items[0].get("status") if cancel_items else None,
                "queued",
                "queue_only submission should return queued state",
            )
            cancel_response = self.call_api(
                f"/api/import/jobs/{cancel_job_id}/cancel",
                method="POST",
            )
            self.soft_assert_equal(
                cancel_response.status_code,
                200,
                "Queued import cancellation endpoint should succeed",
            )
            cancelled_job = cancel_response.json().get("job") or {}
            self.soft_assert_equal(
                cancelled_job.get("status"),
                "cancelled",
                "Queued imports should support durable cancellation",
            )
            repeat_cancel = self.call_api(
                f"/api/import/jobs/{cancel_job_id}/cancel",
                method="POST",
            )
            self.soft_assert_equal(
                repeat_cancel.status_code,
                409,
                "Terminal imports should reject cancellation",
            )
            recent_response = self.call_api(
                "/api/import/jobs",
                params={"limit": 3, "vault": vault.name},
            )
            recent_jobs = recent_response.json().get("jobs") or []
            self.soft_assert_equal(
                recent_jobs[0].get("id") if recent_jobs else None,
                cancel_job_id,
                "Recent import status should return newest jobs first",
            )
            cancelled_response = self.call_api(
                "/api/import/jobs",
                params={"limit": 1, "vault": vault.name, "status": "cancelled"},
            )
            cancelled_payload = cancelled_response.json()
            self.soft_assert_equal(
                [job.get("status") for job in cancelled_payload.get("jobs") or []],
                ["cancelled"],
                "Import job status filtering should happen before the page limit",
            )
            completed_page = self.call_api(
                "/api/import/jobs",
                params={"limit": 1, "vault": vault.name, "status": "completed"},
            ).json()
            completed_cursor = completed_page.get("next_cursor")
            self.soft_assert(
                bool(completed_cursor),
                "A bounded import history page should expose an older-page cursor",
            )
            older_completed_page = self.call_api(
                "/api/import/jobs",
                params={
                    "limit": 1,
                    "vault": vault.name,
                    "status": "completed",
                    "cursor": completed_cursor,
                },
            ).json()
            first_completed_ids = {
                job.get("id") for job in completed_page.get("jobs") or []
            }
            older_completed_ids = {
                job.get("id") for job in older_completed_page.get("jobs") or []
            }
            self.soft_assert(
                first_completed_ids.isdisjoint(older_completed_ids),
                "Import job cursor pages should not repeat jobs",
            )
            static_root = Path(__file__).resolve().parents[4] / "static"
            import_markup = (static_root / "index.html").read_text(encoding="utf-8")
            import_styles = (static_root / "app.css").read_text(encoding="utf-8")
            self.soft_assert(
                all(
                    f'value="{status}" checked' in import_markup
                    for status in ("queued", "processing", "failed")
                ),
                "Import status UI should default to active and failed jobs",
            )
            self.soft_assert(
                "max-height: 24rem" in import_styles
                and "table-layout: auto" in import_styles
                and ".import-job-compact" in import_styles,
                "Import history should be bounded with content-responsive columns",
            )
            self.soft_assert(
                ".import-job-source" in import_styles
                and "max-width: 20rem" in import_styles,
                "Import sources should wrap without dominating the job table",
            )
            import_script = (static_root / "js" / "configuration.js").read_text(
                encoding="utf-8"
            )
            self.soft_assert(
                "data-import-job-edit" in import_script
                and "Edit import settings for job" in import_script
                and "Adjust PDF/OCR settings" in import_script,
                "URL imports should expose a recognizable edit action",
            )
            self.soft_assert(
                "params.set('vault', selectedVault)" in import_script
                and "Select a vault to load its import history" in import_script,
                "Import history should be scoped by the top-level vault selection",
            )
            await get_runtime_context().ingestion_worker.run_once()
            cancelled_status = await tool.function(
                operation="status",
                job_ids=cancel_job_id,
            )
            cancelled_items = (cancelled_status.metadata or {}).get("items") or []
            self.soft_assert_equal(
                cancelled_items[0].get("status") if cancelled_items else None,
                "cancelled",
                "The worker should not process cancelled imports",
            )

            interrupted_submit = await tool.function(
                operation="submit",
                sources="Research/local.pdf",
                queue_only=True,
            )
            interrupted_items = (interrupted_submit.metadata or {}).get("items") or []
            interrupted_job_id = (
                interrupted_items[0].get("job_id") if interrupted_items else None
            )
            self.soft_assert(
                bool(interrupted_job_id and claim_queued_job(interrupted_job_id)),
                "Restart-recovery probe should claim a queued import",
            )
            reconciled_ids = fail_processing_jobs(
                "Import interrupted by an application restart"
            )
            self.soft_assert(
                interrupted_job_id in reconciled_ids,
                "Restart recovery should reconcile orphaned processing imports",
            )
            interrupted_status = await tool.function(
                operation="status", job_ids=interrupted_job_id
            )
            interrupted_status_items = (interrupted_status.metadata or {}).get(
                "items"
            ) or []
            self.soft_assert_equal(
                (
                    interrupted_status_items[0].get("status")
                    if interrupted_status_items
                    else None
                ),
                "failed",
                "Interrupted processing imports should become explicitly failed",
            )

            runtime = get_runtime_context()
            with (
                patch.object(runtime.scheduler, "modify_job") as modify_job,
                patch.object(runtime.scheduler, "wakeup") as wakeup,
            ):
                run_now_response = self.call_api(
                    "/api/import/run-now",
                    method="POST",
                )
            self.soft_assert_equal(
                run_now_response.status_code,
                200,
                "Run now endpoint should accept the scheduler trigger",
            )
            self.soft_assert(
                run_now_response.json().get("queued_count", -1) >= 0,
                "Run now should report queue depth",
            )
            self.soft_assert(
                modify_job.called and wakeup.called,
                "Run now should advance and wake the scheduler-owned worker",
            )

            events = self.validation_events()
            self.soft_assert_event_contains(
                events,
                name="ingestion_job_cancelled",
                expected={"job_id": cancel_job_id, "status": "cancelled"},
            )
            self.soft_assert_event_contains(
                events,
                name="ingestion_worker_triggered",
                expected={"source": "api"},
            )
            self.soft_assert_event_contains(
                events,
                name="content_import_submitted",
                expected={"queue_only": False, "terminal_count": 1},
            )

            failed_url = "https://example.org/reports/failure.pdf"
            with patch(
                "core.ingestion.sources.web.fetch_url_with_curl",
                side_effect=RuntimeError("validation fetch failure"),
            ):
                failed_submit = await tool.function(
                    operation="submit",
                    sources=failed_url,
                )
            failed_items = (failed_submit.metadata or {}).get("items") or []
            self.soft_assert_equal(
                (failed_submit.metadata or {}).get("status"),
                "success",
                "A valid submission should retain its successful tool envelope",
            )
            self.soft_assert_equal(
                failed_items[0].get("status") if failed_items else None,
                "failed",
                "Immediate processing failures should return durable failed jobs",
            )
            self.soft_assert(
                "validation fetch failure"
                in str(failed_items[0].get("error") if failed_items else ""),
                "Immediate failed jobs should expose their durable error",
            )
            failed_job_id = failed_items[0].get("job_id") if failed_items else None
            self.soft_assert_event_contains(
                self.validation_events(),
                name="content_import_submitted",
                expected={
                    "job_ids": [failed_job_id],
                    "queue_only": False,
                    "terminal_count": 1,
                    "failed_count": 1,
                },
            )

            jobs_before_missing_authority = count_jobs()
            with patch(
                "core.tools.content_import.require_current_execution_authority",
                side_effect=RuntimeError("validation authority missing"),
            ):
                missing_authority = await tool.function(
                    operation="submit",
                    sources="Research/local.pdf",
                )
            self.soft_assert_equal(
                (missing_authority.metadata or {}).get("status"),
                "failed",
                "Immediate import should reject an unowned execution boundary",
            )
            self.soft_assert_equal(
                count_jobs(),
                jobs_before_missing_authority,
                "Authority failure must not leave an accepted job queued",
            )

            invalid = await tool.function(
                operation="submit",
                sources="../outside.pdf",
            )
            self.soft_assert_equal(
                (invalid.metadata or {}).get("status"),
                "failed",
                "Traversal should return a structured tool failure",
            )
            escaped_scan = self.call_api(
                "/api/import/scan",
                method="POST",
                data={"vault": "../outside", "queue_only": True},
            )
            self.soft_assert_equal(
                escaped_scan.status_code,
                400,
                "Import inbox scan must reject vault traversal",
            )
        finally:
            ocr_unavailable.stop()
            await self.stop_system()
            self.teardown_scenario()
        self.assert_no_failures()
