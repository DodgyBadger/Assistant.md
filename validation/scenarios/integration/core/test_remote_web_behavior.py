"""Real import/extraction journeys with explicitly simulated external HTTP inputs."""

import json

import pytest

from validation.scenarios.application import Application
from validation.scenarios.support.external_web import external_web
from validation.scenarios.support.ingestion_inputs import require_missing_ocr_secret
from validation.scenarios.support.vault_inputs import document_pdf

pytestmark = pytest.mark.scenario


async def test_remote_import_preserves_destination_provenance_and_content_detection(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = app.vault("RemoteDocuments")
    await app.start()
    require_missing_ocr_secret(app)
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.tools.content_import import ContentImport

    with (
        external_web(monkeypatch, app) as remote,
        use_execution_authority(LOCAL_USER_AUTHORITY),
    ):
        html_url = "https://documents.example/article"
        pdf_url = "https://documents.example/download"
        remote.serve(
            html_url,
            b"<html><title>Field Notes</title><body><p>Durable remote content.</p></body></html>",
        )
        remote.serve(
            pdf_url,
            document_pdf("A real PDF detected from its bytes."),
            headers={"content-type": "application/octet-stream"},
        )
        tool = ContentImport.get_tool(vault_path=str(vault))
        result = await tool.function(
            operation="submit",
            sources=[html_url, pdf_url],
            options={"destination": "Research/Remote"},
        )
        payload = json.loads(str(result.return_value))
        app.evidence.save_json("remote-import-result.json", payload)
        items = payload["items"]
        assert [i["status"] for i in items] == ["completed", "completed"]
        assert [i["source"] for i in items] == [html_url, pdf_url]
        outputs = [vault / i["outputs"][0] for i in items]
        assert all(p.parent == vault / "Research/Remote" for p in outputs)
        assert "Durable remote content." in outputs[0].read_text()
        assert "A real PDF detected from its bytes." in outputs[1].read_text()
        assert html_url in outputs[0].read_text() and pdf_url in outputs[1].read_text()
        from validation.scenarios.support.settings import activity_events

        classified = [
            e["data"]
            for e in activity_events(app)
            if e.get("data", {}).get("event") == "ingestion_remote_classified"
        ]
        assert any(
            e["detected_mime"] == "application/pdf"
            and e["evidence"] == "payload_signature"
            for e in classified
        )
        assert remote.requests == [html_url, pdf_url]


async def test_mixed_extraction_preserves_success_and_rejects_binary_and_private_redirect(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.start()
    from core.web.service import WebCapabilityService

    with external_web(monkeypatch, app) as remote:
        good = "https://documents.example/readable"
        binary = "https://documents.example/binary"
        redirect = "https://documents.example/redirect"
        private = "http://127.0.0.1/private"
        remote.serve(
            good,
            b"<html><title>Keep me</title><body><p>Useful result survives.</p></body></html>",
        )
        remote.serve(
            binary,
            document_pdf("Binary document"),
            headers={"content-type": "application/pdf"},
        )
        remote.serve(redirect, b"", status=302, headers={"location": private})
        result = await WebCapabilityService().extract(
            urls=[good, private, binary, redirect], strategy="curl"
        )
        app.evidence.save_json(
            "mixed-extraction.json",
            {
                "items": [i.__dict__ for i in result.items],
                "failures": [i.__dict__ for i in result.failures],
            },
        )
        assert len(result.items) == 1 and result.items[0].source_url == good
        assert "Useful result survives." in result.items[0].content
        failures = {f.source_url: f for f in result.failures}
        assert set(failures) == {private, binary, redirect}
        assert failures[private].error_type == "WebUrlPolicyError"
        assert failures[redirect].error_type == "WebUrlPolicyError"
        assert failures[binary].error_type == "RuntimeError"
        assert "application/pdf" in failures[binary].error
        assert remote.requests == [good, binary, redirect]


async def test_real_public_policy_rejects_changed_and_mixed_external_dns(
    app: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    await app.start()
    from core.web.errors import WebUrlPolicyError
    from core.web.security import resolve_public_url

    with external_web(monkeypatch, app) as remote:
        url = "https://documents.example/readable"
        remote.serve(url, b"unused")
        assert resolve_public_url(url)[1] == ("93.184.215.14",)
        for answer in [("127.0.0.1",), ("93.184.215.14", "10.0.0.1")]:
            remote.addresses["documents.example"] = answer
            with pytest.raises(WebUrlPolicyError):
                resolve_public_url(url)
        assert remote.requests == []
