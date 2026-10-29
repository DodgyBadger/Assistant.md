"""Real browser import submission reaches actual local PDF processing and writes."""

import re

import pytest
from playwright.async_api import expect

from validation.scenarios.support.browser_journeys import open_document_import

pytestmark = pytest.mark.scenario


async def test_document_import_modal_reports_completed_persisted_output(
    browser_app, ui
):
    form = await open_document_import(
        ui, browser_app.url, browser_app.vault.name, "source.pdf"
    )
    await form.locator("summary").filter(has_text="Options").click()
    await form.get_by_label("PDF strategy").select_option("local_text")
    await form.get_by_role("button", name="Import", exact=True).click()
    await expect(form.get_by_text(re.compile(r"\bcompleted\b"))).to_be_visible(
        timeout=30000
    )
    await expect(form.get_by_text("source.md", exact=True)).to_be_visible()

    output = browser_app.vault / "source.md"
    assert "Browser imported document content." in output.read_text()
    assert (browser_app.vault / "source.pdf").is_file()
    jobs = browser_app.app.request(
        "/api/import/jobs", params={"vault": browser_app.vault.name}
    ).json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "completed"
    assert jobs[0]["selected_strategy"] == "pdf_text"
    assert jobs[0]["outputs"] == ["source.md"]
    browser_app.app.evidence.save_json("import-browser-result.json", jobs[0])
