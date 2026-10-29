"""Real browser import submission reaches actual local PDF processing and writes."""

import asyncio
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


async def test_queue_only_preserves_source_until_dashboard_processes_job(
    browser_app, ui
):
    form = await open_document_import(
        ui, browser_app.url, browser_app.vault.name, "source.pdf"
    )
    await form.locator("summary").filter(has_text="Options").click()
    await form.get_by_label("Queue only", exact=True).check()
    await form.get_by_label("PDF strategy").select_option("local_text")
    await form.get_by_role("button", name="Import", exact=True).click()
    await expect(form.get_by_text("1 import job queued.", exact=True)).to_be_visible()
    jobs = browser_app.app.request(
        "/api/import/jobs", params={"vault": browser_app.vault.name}
    ).json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["status"] == "queued"
    assert not (browser_app.vault / "source.md").exists()
    await ui.goto(browser_app.url)
    await ui.get_by_role("button", name="Dashboard", exact=True).click()
    await ui.locator("#dashboard-import-section > summary").click()
    await ui.get_by_label("Selected vault", exact=True).select_option(
        browser_app.vault.name
    )
    await ui.get_by_role("button", name="Process Queue Now", exact=True).click()
    await expect(ui.locator("#import-jobs-feedback")).to_contain_text(
        "Worker run requested"
    )
    async with asyncio.timeout(30):
        while not (browser_app.vault / "source.md").exists():
            await asyncio.sleep(0.1)
    assert (
        "Browser imported document content."
        in (browser_app.vault / "source.md").read_text()
    )
    await ui.get_by_role("button", name="Refresh Import Status", exact=True).click()
    await ui.locator("#import-jobs-status-filters").get_by_label(
        "Completed", exact=True
    ).check()
    await expect(ui.locator("#import-jobs-list")).to_contain_text("completed")


async def test_import_submission_transport_failure_keeps_options_and_retry_works(
    browser_app, ui
):
    from validation.scenarios.support.browser_fullstack import lose_connection

    form = await open_document_import(
        ui, browser_app.url, browser_app.vault.name, "source.pdf"
    )
    await form.locator("summary").filter(has_text="Options").click()
    await form.get_by_label("PDF strategy").select_option("local_text")
    async with lose_connection(
        ui, browser_app.app, browser_app.url + "/api/import/sources", method="POST"
    ) as dropped:
        await form.get_by_role("button", name="Import", exact=True).click()
        await expect(form.locator("[data-vault-explorer-form-status]")).to_contain_text(
            "Failed"
        )
        await expect(form.get_by_label("PDF strategy")).to_have_value("local_text")
        assert len(dropped) == 1
        assert (
            browser_app.app.request(
                "/api/import/jobs", params={"vault": browser_app.vault.name}
            ).json()["jobs"]
            == []
        )
    await form.get_by_role("button", name="Import", exact=True).click()
    await expect(form.get_by_text(re.compile(r"\bcompleted\b"))).to_be_visible(
        timeout=30000
    )
    assert (browser_app.vault / "source.md").exists()


async def test_upload_and_import_uses_real_file_bytes_and_selected_strategy(
    browser_app, ui
):
    from validation.scenarios.support.browser_fullstack import open_explorer
    from validation.scenarios.support.vault_inputs import document_pdf

    explorer = await open_explorer(ui, browser_app)
    await explorer.locator("[data-vault-explorer-upload-input]").set_input_files(
        {
            "name": "uploaded.pdf",
            "mimeType": "application/pdf",
            "buffer": document_pdf("Uploaded browser document."),
        }
    )
    form = explorer.locator("[data-vault-explorer-upload-form]")
    await form.locator("summary").filter(has_text="Options").click()
    await form.get_by_label("PDF strategy").select_option("local_text")
    await form.get_by_role("button", name="Upload & import", exact=True).click()
    await expect(explorer).to_contain_text("1 file uploaded. 1 import job accepted")
    await ui.goto(browser_app.url)
    await ui.get_by_role("button", name="Dashboard", exact=True).click()
    await ui.locator("#dashboard-import-section > summary").click()
    await ui.get_by_label("Selected vault", exact=True).select_option(
        browser_app.vault.name
    )
    await ui.locator("#import-jobs-status-filters").get_by_label(
        "Completed", exact=True
    ).check()
    await expect(ui.locator("#import-jobs-list")).to_contain_text(
        "uploaded.md", timeout=30000
    )
    assert (browser_app.vault / "uploaded.pdf").is_file()
    assert (
        "Uploaded browser document." in (browser_app.vault / "uploaded.md").read_text()
    )


async def test_import_options_reset_uses_saved_defaults_without_changing_settings(
    browser_app, ui
):
    form = await open_document_import(
        ui, browser_app.url, browser_app.vault.name, "source.pdf"
    )
    await form.locator("summary").filter(has_text="Options").click()
    await form.get_by_label("PDF strategy").select_option("ocr")
    await form.get_by_label("OCR images").select_option("false")
    await form.get_by_label("Queue only", exact=True).check()
    await form.get_by_role(
        "button", name="Reset to current defaults", exact=True
    ).click()
    await expect(form.get_by_label("PDF strategy")).to_have_value("")
    await expect(form.get_by_label("OCR images")).to_have_value("")
    await expect(form.get_by_label("Queue only", exact=True)).not_to_be_checked()
    assert not browser_app.app.request(
        "/api/import/jobs", params={"vault": browser_app.vault.name}
    ).json()["jobs"]


async def test_failed_document_processing_is_visible_and_source_is_preserved(
    browser_app, ui
):
    (browser_app.vault / "broken.pdf").write_bytes(b"This is not a PDF")
    form = await open_document_import(
        ui, browser_app.url, browser_app.vault.name, "broken.pdf"
    )
    await form.locator("summary").filter(has_text="Options").click()
    await form.get_by_label("PDF strategy").select_option("local_text")
    await form.get_by_role("button", name="Import", exact=True).click()
    from validation.scenarios.support.browser_imports import wait_import_status

    jobs = await wait_import_status(browser_app, "failed")
    await expect(form.locator("[data-vault-explorer-form-status]")).to_contain_text(
        jobs[0]["error"]
    )
    assert len(jobs) == 1 and jobs[0]["status"] == "failed" and jobs[0]["error"]
    assert not (browser_app.vault / "broken.md").exists()
    assert (browser_app.vault / "broken.pdf").read_bytes() == b"This is not a PDF"


async def test_url_import_executes_real_admission_extraction_and_persistence(
    browser_app, ui, monkeypatch
):
    from validation.scenarios.support.browser_fullstack import open_explorer
    from validation.scenarios.support.external_web import external_web
    from validation.scenarios.support.vault_inputs import document_pdf

    # Only external HTTP bytes and DNS are controlled: the live site would need
    # network access and mutable content. URL admission, import orchestration,
    # parsing and durable writes are the real application implementations.
    url = "https://browser-validation.example/remote.pdf"
    with external_web(monkeypatch, browser_app.app) as remote:
        remote.serve(
            url,
            document_pdf("Remote document evidence."),
            headers={"content-type": "application/pdf"},
        )
        explorer = await open_explorer(ui, browser_app)
        await explorer.get_by_role("button", name="Import", exact=True).click()
        await explorer.get_by_role("menuitem", name="Import URL", exact=True).click()
        form = explorer.get_by_role("form", name="Import to Markdown")
        await form.get_by_label("Document URL", exact=True).fill(url)
        await form.locator("summary").filter(has_text="Options").click()
        await form.get_by_label("PDF strategy").select_option("local_text")
        await form.get_by_role("button", name="Import", exact=True).click()
        await expect(form.get_by_text(re.compile(r"\bcompleted\b"))).to_be_visible(
            timeout=30000
        )
        jobs = browser_app.app.request(
            "/api/import/jobs", params={"vault": browser_app.vault.name}
        ).json()["jobs"]
        assert len(jobs) == 1 and jobs[0]["status"] == "completed"
        assert jobs[0]["source_uri"] == url and remote.requests == [url]
        assert (
            "Remote document evidence."
            in (browser_app.vault / jobs[0]["outputs"][0]).read_text()
        )


async def test_successful_import_reports_real_strategy_fallback_warning(
    browser_app, ui
):
    from validation.scenarios.support.browser_fullstack import setting

    setting(
        browser_app.app, "ingestion_pdf_default_strategies", '["pdf_ocr", "pdf_text"]'
    )
    form = await open_document_import(
        ui, browser_app.url, browser_app.vault.name, "source.pdf"
    )
    await form.get_by_role("button", name="Import", exact=True).click()
    await expect(form.get_by_text(re.compile(r"\bcompleted\b"))).to_be_visible(
        timeout=30000
    )
    jobs = browser_app.app.request(
        "/api/import/jobs", params={"vault": browser_app.vault.name}
    ).json()["jobs"]
    assert len(jobs) == 1 and jobs[0]["status"] == "completed"
    assert "pdf_ocr:missing_secret:" in jobs[0]["fallback_reason"]
    await expect(form.locator("[data-vault-explorer-form-status]")).to_contain_text(
        jobs[0]["fallback_reason"]
    )
    assert (
        "Browser imported document content."
        in (browser_app.vault / "source.md").read_text()
    )
