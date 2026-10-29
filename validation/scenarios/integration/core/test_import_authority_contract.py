"""Immediate imports require real ingress and execution authority before enqueue."""

from __future__ import annotations

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.authentication import (
    OWNER_SECRET,
    authentication_application,
    authentication_client,
)
from validation.scenarios.support.vault_inputs import document_pdf

pytestmark = pytest.mark.scenario


async def test_unauthorized_immediate_import_never_creates_a_job(
    app: Application,
) -> None:
    vault = app.vault("ImportAuthorityVault")
    source = vault / "source.pdf"
    source.write_bytes(document_pdf("Import authority fixture"))
    app.evidence.register_secret(OWNER_SECRET)
    await app.start()
    from pydantic_ai.messages import ToolReturn

    from core.identity import get_current_execution_authority
    from core.ingestion.jobs import count_jobs
    from core.tools.content_import import ContentImport

    assert get_current_execution_authority() is None
    before = count_jobs()
    tool = ContentImport.get_tool(str(vault))
    denied_tool = await tool.function(
        operation="submit", sources=["source.pdf"], queue_only=False
    )
    assert isinstance(denied_tool, ToolReturn)
    assert denied_tool.metadata["error_type"] == "RuntimeError"
    assert (
        denied_tool.metadata["status"] == "failed"
        and denied_tool.metadata["retryable"] is False
    )
    assert count_jobs() == before and source.exists()
    application = authentication_application(
        app, "owner_token", ASSISTANTMD_AUTH_SECRET=OWNER_SECRET
    )
    request = {
        "vault": vault.name,
        "sources": ["source.pdf"],
        "destination": "Imported",
        "queue_only": False,
        "strategies": ["pdf_text"],
    }
    async with authentication_client(app, application) as client:
        for headers in ({}, {"Authorization": "Bearer incorrect-owner-token"}):
            rejected = await client.post(
                "/api/import/sources", json=request, headers=headers
            )
            assert rejected.status_code == 401
            assert count_jobs() == before
        accepted = await client.post(
            "/api/import/sources",
            json=request,
            headers={"Authorization": f"Bearer {OWNER_SECRET}"},
        )
        assert accepted.status_code == 200, accepted.text
        jobs = accepted.json()["jobs_created"]
        assert len(jobs) == 1 and count_jobs() == before + 1
        status = await client.get(
            f"/api/import/jobs/{jobs[0]['id']}",
            headers={"Authorization": f"Bearer {OWNER_SECRET}"},
        )
        assert status.status_code == 200 and status.json()["status"] == "completed"
        assert status.json()["outputs"] == ["Imported/source.md"]
        assert "Import authority fixture" in (vault / "Imported/source.md").read_text(
            encoding="utf-8"
        )
