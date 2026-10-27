"""Validate scheduled ingestion worker execution-task ownership."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from core.runtime.state import get_runtime_context
from validation.support.base_scenario import BaseScenario


class ImportPipelineSchedulerScenario(BaseScenario):
    """Validate queued imports are processed through scheduler ingestion tasks."""

    async def test_scenario(self):
        vault = self.create_vault("ImportPipelineSchedulerVault")
        pdf_bytes = self.make_pdf("Scheduled import validation\nLine two")
        import_path = vault / "AssistantMD" / "Import" / "scheduled.pdf"
        import_path.parent.mkdir(parents=True, exist_ok=True)
        import_path.write_bytes(pdf_bytes)

        await self.start_system()

        response = self.call_api(
            "/api/import/scan",
            method="POST",
            data={"vault": vault.name, "queue_only": True},
        )
        self.soft_assert_equal(
            response.status_code, 200, "Queued import scan should succeed"
        )
        jobs = response.json().get("jobs_created") or []
        self.soft_assert_equal(len(jobs), 1, "One queued import job should be created")
        job = jobs[0]
        job_id = job.get("id")
        self.soft_assert_equal(
            job.get("status"), "queued", "Import job should remain queued"
        )

        await get_runtime_context().ingestion_worker.run_once()

        output_path: Path | None = None
        for _ in range(100):
            outputs = sorted((vault / "Imported").rglob("*.md"))
            if outputs:
                output_path = outputs[0]
                break
            await asyncio.sleep(0.02)

        self.soft_assert(
            output_path is not None, "Scheduled import should create markdown output"
        )
        if output_path is not None:
            content = output_path.read_text(encoding="utf-8")
            self.soft_assert(
                "Scheduled import validation" in content,
                "Scheduled import output should contain extracted text",
            )
        self.soft_assert(
            not import_path.exists(),
            "Scheduled import should remove the source file after processing",
        )

        tasks = await get_runtime_context().task_coordinator.list_tasks(
            kind="ingestion"
        )
        matching_task = next(
            (
                task
                for task in tasks
                if task.metadata.get("job_id") == job_id and task.source == "scheduler"
            ),
            None,
        )
        self.soft_assert(
            matching_task is not None,
            "Scheduled import should create a scheduler ingestion execution task",
        )
        if matching_task is not None:
            self.soft_assert_equal(
                matching_task.status,
                "completed",
                "Scheduled ingestion execution task should complete",
            )

        await self.stop_system()
        self.teardown_scenario()
        self.assert_no_failures()
