"""Real document inputs, isolated credentials and observable ingestion persistence."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from validation.scenarios.application import Application
from validation.scenarios.support.vault_inputs import document_pdf


def pdf_source(vault: Path, relative_path: str, text: str) -> Path:
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(document_pdf(text))
    return path


def require_missing_ocr_secret(app: Application) -> None:
    response = app.request("/api/system/secrets/MISTRAL_API_KEY", method="DELETE")
    assert response.status_code == 200, response.text
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.settings.secrets_store import secret_has_value

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        assert not secret_has_value("MISTRAL_API_KEY")


def manifest_matches(
    app: Application, vault_name: str, path: str, *, deleted: bool
) -> bool:
    with sqlite3.connect(app.root / "system/vault_state.db") as conn:
        row = conn.execute(
            "SELECT vault_id FROM vaults WHERE current_name = ?", (vault_name,)
        ).fetchone()
        assert row is not None
        manifest = conn.execute(
            "SELECT deleted_at FROM vault_files WHERE vault_id = ? AND path = ?",
            (row[0], path),
        ).fetchone()
        return manifest is not None and (manifest[0] is not None) is deleted


async def process_file_job(
    app: Application, vault_name: str, source: str, *, consume: bool
):
    from core.identity import SYSTEM_AUTHORITY
    from core.ingestion.models import SourceKind
    from core.ingestion.task_execution import process_ingestion_job_in_task
    from core.runtime.execution_tasks import ExecutionTaskSource
    from core.runtime.state import get_runtime_context

    runtime = get_runtime_context()
    job = runtime.ingestion.enqueue_job(
        source_uri=source,
        vault=vault_name,
        source_type=SourceKind.FILE.value,
        mime_hint="application/pdf",
        options={"consume_source": consume},
    )
    assert runtime.ingestion.claim_job(job.id)
    await process_ingestion_job_in_task(
        task_coordinator=runtime.task_coordinator,
        process_job_fn=runtime.ingestion.process_job,
        job_id=job.id,
        vault=vault_name,
        source=ExecutionTaskSource.API,
        authority=SYSTEM_AUTHORITY,
    )
    result = runtime.ingestion.get_job(job.id)
    assert result is not None
    app.evidence.append(
        "processed-imports.jsonl",
        {"job_id": result.id, "status": result.status, "outputs": result.outputs},
    )
    return result


@contextmanager
def deny_directory_deletions(path: Path):
    """Real owner permission denial permits reads while preventing source unlink."""
    original_mode = path.stat().st_mode & 0o777
    path.chmod(0o555)
    try:
        yield
    finally:
        path.chmod(original_mode)


async def wait_import_terminal(app: Application, job_id: int):
    async with asyncio.timeout(30):
        while True:
            response = await app.request_async(f"/api/import/jobs/{job_id}")
            assert response.status_code == 200, response.text
            payload = response.json()
            job = payload.get("job", payload)
            if job["status"] in {"completed", "failed", "cancelled"}:
                return job
            await asyncio.sleep(0.05)
