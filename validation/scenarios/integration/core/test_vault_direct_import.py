"""Durable direct source imports through the shared ingestion pipeline."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.vault_inputs import (
    document_pdf,
    import_source,
    import_status,
)

pytestmark = pytest.mark.scenario


async def test_direct_pdf_import_snapshots_options_preserves_sources_and_numbers_outputs(
    app: Application,
) -> None:
    vault = app.vault("VaultDirectImportVault")
    app.write(vault, "Uploads/notes.txt", "unsupported")
    source = vault / "Uploads/source.pdf"
    source.write_bytes(document_pdf("Direct import validation"))
    await app.start()
    from core.runtime.state import get_runtime_context

    queued = import_source(
        app, vault.name, destination="Library", queue_only=True, strategies=["pdf_text"]
    )
    assert queued["request_options"] == {
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
    }
    assert queued["can_resubmit"] is False
    durable = get_runtime_context().ingestion.get_job(queued["id"])
    assert durable is not None
    assert durable.options["output_path_pattern"] == "Library"
    assert durable.options["consume_source"] is False
    activity = app.request(
        "/api/system/activity-log?limit=100&tag=api-services"
    ).json()["entries"]
    assert any(
        (entry.get("data") or {}).get("event") == "ingestion_jobs_submitted"
        and entry["data"].get("job_ids") == [queued["id"]]
        and entry["data"].get("vault_name") == vault.name
        and entry["data"].get("status") == "queued"
        for entry in activity
    )

    checkpoint = app.events.checkpoint()
    immediate = import_source(app, vault.name, destination="", strategies=["pdf_text"])
    status = import_status(app, immediate["id"])
    assert status["status"] == "completed"
    assert status["outputs"] == ["source.md"]
    assert status["can_resubmit"] is True
    assert source.exists()
    assert "Direct import validation" in (vault / "source.md").read_text()
    app.events.require(
        after=checkpoint,
        name="execution_task_completed",
        fields={
            "kind": "ingestion",
            "label": f"ingestion:{immediate['id']}",
            "status": "completed",
        },
    )
    activity = app.request("/api/system/activity-log?limit=200&tag=ingestion").json()[
        "entries"
    ]
    lifecycle = [
        entry["data"]
        for entry in activity
        if (entry.get("data") or {}).get("job_id") == immediate["id"]
    ]
    assert {
        ("ingestion_job_started", "started"),
        ("ingestion_strategies_resolved", "selected"),
        ("ingestion_job_completed", "completed"),
    }.issubset({(event.get("event"), event.get("status")) for event in lifecycle})
    assert all(event["vault_name"] == vault.name for event in lifecycle)
    assert "Direct import validation" not in str(lifecycle)
    collision = import_source(app, vault.name, destination="", strategies=["pdf_text"])
    assert import_status(app, collision["id"])["outputs"] == ["source_1.md"]

    assert (
        app.request(
            "/api/system/settings/general/ingestion_pdf_default_mode",
            "PUT",
            {"value": "page_images"},
        ).status_code
        == 200
    )
    defaulted = import_source(app, vault.name, destination="PageDefault")
    outputs = import_status(app, defaulted["id"])["outputs"]
    assert "PageDefault/source.md" in outputs
    assert any(path.endswith("/pages/page_0001.png") for path in outputs)
    original_source = source.read_bytes()
    original_artifacts = {path: (vault / path).read_bytes() for path in outputs}
    numbered = import_source(app, vault.name, destination="PageDefault")
    numbered_outputs = import_status(app, numbered["id"])["outputs"]
    assert "PageDefault/source_1.md" in numbered_outputs
    page = "PageDefault/assets/source_1/pages/page_0001.png"
    assert page in numbered_outputs
    assert (vault / page).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert (
        "(assets/source_1/pages/page_0001.png)"
        in (vault / "PageDefault/source_1.md").read_text()
    )
    assert source.read_bytes() == original_source
    assert all(
        (vault / path).read_bytes() == content
        for path, content in original_artifacts.items()
    )

    from core.ingestion.jobs import IngestionJobCreate, count_jobs, create_jobs

    before = count_jobs()
    invalid = app.request(
        "/api/import/sources",
        "POST",
        {
            "vault": vault.name,
            "sources": ["Uploads/source.pdf", "Uploads/notes.txt"],
            "destination": "Library",
            "queue_only": True,
        },
    )
    assert invalid.status_code == 400
    assert count_jobs() == before
    with pytest.raises(RuntimeError):
        create_jobs(
            [
                IngestionJobCreate(
                    source_uri="valid-source.pdf",
                    vault=vault.name,
                    source_type="file",
                    mime_hint=None,
                    options={},
                ),
                IngestionJobCreate(source_uri=None, vault=vault.name, source_type="file", mime_hint=None, options={}),  # type: ignore[arg-type]
            ]
        )
    assert count_jobs() == before
    legacy = get_runtime_context().ingestion.enqueue_job(
        source_uri="https://example.test/legacy",
        vault=vault.name,
        source_type="url",
        mime_hint=None,
        options={"consume_source": False},
    )
    assert (
        import_status(app, legacy.id)["request_options"]["destination"] == "Imported/"
    )
    capabilities = app.request("/api/metadata").json()["ingestion_capabilities"][
        "file_import"
    ]
    assert capabilities["features"] == [
        ".jpeg",
        ".jpg",
        ".pdf",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    ]
