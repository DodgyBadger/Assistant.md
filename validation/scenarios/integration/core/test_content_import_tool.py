"""Real content import submission, cancellation, history and startup/worker ownership."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import ToolReturn

from validation.scenarios.application import Application
from validation.scenarios.support.ingestion_inputs import (
    pdf_source,
    require_missing_ocr_secret,
    wait_import_terminal,
)

pytestmark = pytest.mark.scenario


async def test_content_import_local_lifecycle_and_scheduler_trigger(
    app: Application,
) -> None:
    vault = app.vault("ContentImportToolVault")
    local_pdf = pdf_source(
        vault, "Research/local.pdf", "Local content import validation"
    )
    pdf_source(vault, "Research/second.pdf", "Second content import validation")
    await app.start()
    require_missing_ocr_secret(app)
    from core.authoring.shared.tool_binding import resolve_tool_binding
    from core.identity import (
        LOCAL_USER_AUTHORITY,
        LOCAL_USER_PRINCIPAL_ID,
        use_execution_authority,
    )
    from core.ingestion.jobs import claim_queued_job
    from core.runtime.state import get_runtime_context
    from core.tools.content_import import ContentImport

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        binding = resolve_tool_binding(["content_import"], vault_path=str(vault))
        assert binding.tool_names() == [
            "content_import"
        ], "content_import should bind for Monty direct tool calls"
        tool = ContentImport.get_tool(vault_path=str(vault))
        submit = await tool.function(
            operation="submit",
            sources="Research/local.pdf",
            options={"destination": "Research/Library"},
        )
        assert isinstance(
            submit, ToolReturn
        ), "submit should return a structured ToolReturn"
        submit_items = (submit.metadata or {}).get("items") or []
        assert len(submit_items) == 1, "submit should create one job"
        local_job_id = submit_items[0].get("job_id") if submit_items else None
        submit_payload = json.loads(str(submit.return_value))
        assert (
            submit_payload.get("items") == submit_items
        ), "submit should expose job ids and records in the agent-visible result"
        assert (
            submit_items[0].get("status") if submit_items else None
        ) == "completed", "submit should process immediately by default"
        status = await tool.function(operation="status", job_ids=local_job_id)
        status_items = (status.metadata or {}).get("items") or []
        status_payload = json.loads(str(status.return_value))
        assert (
            status_payload.get("items") == status_items
        ), "status should expose job records in the agent-visible result"
        assert (
            status_items[0].get("status") if status_items else None
        ) == "completed", "status should expose terminal ingestion state"
        assert (
            status_items[0].get("selected_strategy") if status_items else None
        ) == "pdf_text", "status should expose the selected extraction strategy"
        assert (
            status_items[0].get("selected_provider") if status_items else None
        ) == "local", "status should expose the selected extraction provider"
        assert (status_items[0].get("strategy_attempts") if status_items else None) == [
            "pdf_ocr",
            "pdf_text",
        ], "status should expose extraction attempts"
        assert (
            status_items[0].get("fallback_reason") if status_items else None
        ) == "pdf_ocr:missing_secret:MISTRAL_API_KEY", (
            "status should explain why OCR fell back to local extraction"
        )
        local_outputs = (status_items[0].get("outputs") if status_items else []) or []
        assert local_outputs == [
            "Research/Library/local.md"
        ], "Import Markdown should land directly in the per-job destination"
        assert (
            local_pdf.exists()
        ), "content_import must preserve a vault-relative source"
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
        assert (
            local_task.source if local_task else None
        ) == "tool", "Immediate content import should be attributed to the tool"
        assert (
            local_task.principal_id if local_task else None
        ) == LOCAL_USER_PRINCIPAL_ID, (
            "Immediate content import should retain the current principal"
        )
        batch_submit = await tool.function(
            operation="submit",
            sources=["Research/local.pdf", "Research/second.pdf"],
            options={"destination": "Research/Batch"},
        )
        batch_items = (batch_submit.metadata or {}).get("items") or []
        assert [item.get("status") for item in batch_items] == [
            "completed",
            "completed",
        ], "Immediate batch submission should return terminal results"
        cancel_submit = await tool.function(
            operation="submit", sources="Research/local.pdf", queue_only=True
        )
        cancel_items = (cancel_submit.metadata or {}).get("items") or []
        cancel_job_id = cancel_items[0].get("job_id") if cancel_items else None
        assert (
            cancel_items[0].get("status") if cancel_items else None
        ) == "queued", "queue_only submission should return queued state"
        queued_job = get_runtime_context().ingestion.get_job(cancel_job_id)
        queued_options = queued_job.options or {} if queued_job else {}
        assert (
            "pdf_mode" in queued_options
            and "pdf_strategies" in queued_options
            and ("output_path_pattern" in queued_options)
            and (
                "ocr_capture_images" in (queued_options.get("extractor_options") or {})
            )
        ), "Queue-only tool submissions should snapshot effective import defaults"
        cancel_response = app.request(
            f"/api/import/jobs/{cancel_job_id}/cancel", method="POST"
        )
        assert (
            cancel_response.status_code == 200
        ), "Queued import cancellation endpoint should succeed"
        cancelled_job = cancel_response.json().get("job") or {}
        assert (
            cancelled_job.get("status") == "cancelled"
        ), "Queued imports should support durable cancellation"
        repeat_cancel = app.request(
            f"/api/import/jobs/{cancel_job_id}/cancel", method="POST"
        )
        assert (
            repeat_cancel.status_code == 409
        ), "Terminal imports should reject cancellation"
        recent_response = app.request(
            "/api/import/jobs", params={"limit": 3, "vault": vault.name}
        )
        recent_jobs = recent_response.json().get("jobs") or []
        assert (
            recent_jobs[0].get("id") if recent_jobs else None
        ) == cancel_job_id, "Recent import status should return newest jobs first"
        cancelled_response = app.request(
            "/api/import/jobs",
            params={"limit": 1, "vault": vault.name, "status": "cancelled"},
        )
        cancelled_payload = cancelled_response.json()
        assert [job.get("status") for job in cancelled_payload.get("jobs") or []] == [
            "cancelled"
        ], "Import job status filtering should happen before the page limit"
        completed_page = app.request(
            "/api/import/jobs",
            params={"limit": 1, "vault": vault.name, "status": "completed"},
        ).json()
        completed_cursor = completed_page.get("next_cursor")
        assert bool(
            completed_cursor
        ), "A bounded import history page should expose an older-page cursor"
        older_completed_page = app.request(
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
        assert first_completed_ids.isdisjoint(
            older_completed_ids
        ), "Import job cursor pages should not repeat jobs"
        await get_runtime_context().ingestion_worker.run_once()
        cancelled_status = await tool.function(
            operation="status", job_ids=cancel_job_id
        )
        cancelled_items = (cancelled_status.metadata or {}).get("items") or []
        assert (
            cancelled_items[0].get("status") if cancelled_items else None
        ) == "cancelled", "The worker should not process cancelled imports"
        interrupted_submit = await tool.function(
            operation="submit", sources="Research/local.pdf", queue_only=True
        )
        interrupted_items = (interrupted_submit.metadata or {}).get("items") or []
        interrupted_job_id = (
            interrupted_items[0].get("job_id") if interrupted_items else None
        )
        assert bool(
            interrupted_job_id and claim_queued_job(interrupted_job_id)
        ), "Restart-recovery probe should claim a queued import"
        await app.controller.restart_system()
        interrupted_status = await tool.function(
            operation="status", job_ids=interrupted_job_id
        )
        interrupted_status_items = (interrupted_status.metadata or {}).get(
            "items"
        ) or []
        assert (
            interrupted_status_items[0].get("status")
            if interrupted_status_items
            else None
        ) == "failed", "Interrupted processing imports should become explicitly failed"
        triggered = await tool.function(
            operation="submit", sources="Research/second.pdf", queue_only=True
        )
        triggered_id = triggered.metadata["items"][0]["job_id"]
        checkpoint = app.events.checkpoint()
        run_now = await app.request_async("/api/import/run-now", method="POST")
        assert run_now.status_code == 200, run_now.text
        assert run_now.json()["queued_count"] >= 1
        triggered_job = await wait_import_terminal(app, triggered_id)
        assert triggered_job["status"] == "completed"
        tasks = await get_runtime_context().task_coordinator.list_tasks(
            kind="ingestion"
        )
        triggered_task = next(
            task for task in tasks if task.metadata.get("job_id") == triggered_id
        )
        assert triggered_task.source == "scheduler"
        app.events.require("ingestion_worker_triggered", after=checkpoint, source="api")
        app.events.require(
            "ingestion_job_cancelled", job_id=cancel_job_id, status="cancelled"
        )
        app.events.require(
            "content_import_submitted", queue_only=False, terminal_count=1
        )


async def test_content_import_real_fetch_failure_and_authority_path_denials(
    app: Application,
) -> None:
    vault = app.vault("ContentImportFailureVault")
    pdf_source(vault, "Research/local.pdf", "Local content import validation")
    await app.start()
    require_missing_ocr_secret(app)
    from core.identity import (
        LOCAL_USER_AUTHORITY,
        get_current_execution_authority,
        use_execution_authority,
    )
    from core.ingestion.jobs import count_jobs
    from core.tools.content_import import ContentImport

    tool = ContentImport.get_tool(vault_path=str(vault))
    assert get_current_execution_authority() is None
    before = count_jobs()
    unowned = await tool.function(operation="submit", sources="Research/local.pdf")
    assert unowned.metadata["status"] == "failed"
    assert count_jobs() == before
    with use_execution_authority(LOCAL_USER_AUTHORITY):
        # Real outbound policy failure replaces a fabricated network exception.
        failed = await tool.function(
            operation="submit", sources="http://127.0.0.1/reports/failure.pdf"
        )
        assert failed.metadata["status"] == "success"
        failed_items = failed.metadata["items"]
        assert len(failed_items) == 1 and failed_items[0]["status"] == "failed"
        assert (
            "Local or private network targets are not allowed"
            in failed_items[0]["error"]
        )
        app.events.require(
            "content_import_submitted",
            job_ids=[failed_items[0]["job_id"]],
            queue_only=False,
            terminal_count=1,
            failed_count=1,
        )
        invalid = await tool.function(operation="submit", sources="../outside.pdf")
        assert invalid.metadata["status"] == "failed"
    escaped = app.request(
        "/api/import/scan",
        method="POST",
        data={"vault": "../outside", "queue_only": True},
    )
    assert escaped.status_code == 400
