"""Actual PDF inbox import records extraction, cleanup and durable vault activity."""

from __future__ import annotations

import pytest

from validation.pytest_scenarios.application import Application
from validation.pytest_scenarios.support.ingestion_inputs import (
    deny_directory_deletions,
    manifest_matches,
    pdf_source,
    process_file_job,
    require_missing_ocr_secret,
)

pytestmark = pytest.mark.scenario


async def test_pdf_inbox_import_and_preserved_source(app: Application) -> None:
    vault = app.vault("ImportPipelineVault")
    import_path = pdf_source(
        vault, "AssistantMD/Import/sample.pdf", "Import validation\nLine two"
    )
    await app.start()
    require_missing_ocr_secret(app)
    response = app.request(
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
    assert not import_path.exists(), "Source file should be cleaned up"
    sample_path = vault / sample_rel_path
    assert sample_path.exists(), f"Expected {sample_rel_path} to be created"
    sample_content = sample_path.read_text()
    assert "Import validation" in sample_content
    assert "mime: application/pdf" in sample_content
    system_activity = app.request("/api/system/activity-log?limit=200")
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
    ) in observed_lifecycle, "Import completion should be visible in System Activity"
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
    assert manifest_matches(
        app, vault.name, sample_rel_path, deleted=False
    ), "Imported file should be tracked in vault state"
    assert manifest_matches(
        app, vault.name, "AssistantMD/Import/sample.pdf", deleted=True
    ), "Cleaned-up source file should be marked deleted in vault state"
    activity_response = app.request(f"/api/vaults/{vault.name}/activity")
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
    research_path = pdf_source(
        vault, "Research/preserved.pdf", "Preserved source validation"
    )
    preserved = await process_file_job(
        app, vault.name, "Research/preserved.pdf", consume=False
    )
    assert preserved.status == "completed"
    assert research_path.exists()
    assert len(preserved.outputs) == 1
    assert "Preserved source validation" in (vault / preserved.outputs[0]).read_text()
    app.events.require(
        "vault_mutation_recorded", path=sample_rel_path, operation="write"
    )


async def test_distinct_real_source_cleanup_failures_retain_outputs_and_diagnostics(
    app: Application,
) -> None:
    vault = app.vault("ImportCleanupFailureVault")
    sources = [
        pdf_source(
            vault, f"AssistantMD/Import/cleanup-{suffix}.pdf", f"cleanup {suffix}"
        )
        for suffix in ("one", "two")
    ]
    await app.start()
    require_missing_ocr_secret(app)
    jobs = []
    with deny_directory_deletions(sources[0].parent):
        for source in sources:
            job = await process_file_job(
                app, vault.name, source.relative_to(vault).as_posix(), consume=True
            )
            jobs.append(job)
            assert job.status == "completed"
            assert source.exists()
            assert job.outputs and all(
                (vault / output).exists() for output in job.outputs
            )
            from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
            from core.runtime.state import get_runtime_context

            # Preserve the source's direct cleanup error contract with the real
            # denied unlink; duplicate warnings keep each job's own identity.
            with (
                use_execution_authority(LOCAL_USER_AUTHORITY),
                pytest.raises(RuntimeError, match="source cleanup failed"),
            ):
                get_runtime_context().ingestion._cleanup_source_file_if_requested(
                    job=job, source_path=source, vault=vault.name
                )
    response = app.request(
        "/api/system/activity-log", params={"limit": 200, "tag": "ingestion"}
    )
    assert response.status_code == 200, response.text
    failures = [
        entry["data"]
        for entry in response.json()["entries"]
        if entry.get("data", {}).get("event") == "ingestion_source_cleanup"
        and entry["data"].get("status") == "failed"
    ]
    assert {entry["issue"] for entry in failures} == {
        f"ingestion_source_cleanup:{job.id}" for job in jobs
    }
    assert len(failures) == 2
    assert all(entry["error_type"] == "PermissionError" for entry in failures)
