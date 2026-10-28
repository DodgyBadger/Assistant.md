"""Controlled document inputs and multipart transport for Explorer journeys."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from validation.scenarios.application import Application


def document_pdf(text: str) -> bytes:
    import fitz

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), text)
        return document.tobytes()


def upload(
    app: Application,
    vault_name: str,
    path: str,
    content: bytes,
    *,
    filename: str = "source.pdf",
    extra_part: bool = False,
) -> Any:
    files = [("file", (filename, content, "application/pdf"))]
    if extra_part:
        files.append(("unexpected", ("second.pdf", content, "application/pdf")))
    response = app.controller.get_api_test_client().post(
        f"/api/vaults/{vault_name}/files/upload",
        params={"path": path},
        files=files,
    )
    app.evidence.append(
        "uploads.jsonl",
        {
            "path": path,
            "filename": filename,
            "status": response.status_code,
            "response": response.json(),
        },
    )
    return response


def import_source(app: Application, vault_name: str, **options: Any) -> dict[str, Any]:
    response = app.request(
        "/api/import/sources",
        method="POST",
        data={"vault": vault_name, "sources": ["Uploads/source.pdf"], **options},
    )
    assert response.status_code == 200, response.text
    job = response.json()["jobs_created"][0]
    assert job["status"] == "queued"
    return job


def import_status(app: Application, job_id: str) -> dict[str, Any]:
    response = app.request(f"/api/import/jobs/{job_id}")
    assert response.status_code == 200, response.text
    return response.json()
