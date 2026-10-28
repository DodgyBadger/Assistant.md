"""The real queued PDF worker owns a scheduler ingestion task and durable output."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.ingestion_inputs import (
    pdf_source,
    require_missing_ocr_secret,
)

pytestmark = pytest.mark.scenario


async def test_scheduler_worker_processes_queued_pdf_with_owned_task(
    app: Application,
) -> None:
    vault = app.vault("ImportPipelineSchedulerVault")
    source = pdf_source(
        vault,
        "AssistantMD/Import/scheduled.pdf",
        "Scheduled import validation\nLine two",
    )
    await app.start()
    require_missing_ocr_secret(app)
    from core.runtime.state import get_runtime_context

    response = app.request(
        "/api/import/scan",
        method="POST",
        data={"vault": vault.name, "queue_only": True},
    )
    assert response.status_code == 200, response.text
    jobs = response.json()["jobs_created"]
    assert len(jobs) == 1
    job_id = jobs[0]["id"]
    assert jobs[0]["status"] == "queued"
    checkpoint = app.events.checkpoint()
    runtime = get_runtime_context()
    await runtime.ingestion_worker.run_once()
    job = runtime.ingestion.get_job(job_id)
    assert job is not None and job.status == "completed"
    assert job.outputs
    assert "Scheduled import validation" in (vault / job.outputs[0]).read_text(
        encoding="utf-8"
    )
    assert not source.exists()
    tasks = await runtime.task_coordinator.list_tasks(kind="ingestion")
    matching = [
        task
        for task in tasks
        if task.metadata.get("job_id") == job_id and task.source == "scheduler"
    ]
    assert len(matching) == 1
    assert matching[0].status == "completed"
    app.events.require(
        "execution_task_completed",
        after=checkpoint,
        task_id=matching[0].task_id,
        status="completed",
    )
