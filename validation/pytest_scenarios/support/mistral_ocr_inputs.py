"""Actual local OCR provider responses and import workloads."""

from __future__ import annotations

import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from validation.pytest_scenarios.application import Application

OCR_KEY = "SECRET_OCR_KEY_SENTINEL"
PRIVATE_DOCUMENT = "PRIVATE_PROVIDER_DOCUMENT_SENTINEL"


class OCRProvider(ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), OCRHandler)
        self.calls = []
        self.status = 200
        self.response = {
            "model": "mistral-ocr-4-0",
            "usage_info": {"pages_processed": 1},
            "pages": [
                {
                    "index": 0,
                    "markdown": "# Direct OCR",
                    "header": "Report header",
                    "blocks": [{"type": "title", "content": "Direct OCR"}],
                    "confidence_scores": {"average_page_confidence_score": 0.99},
                }
            ],
        }

    @property
    def endpoint(self):
        return f"http://127.0.0.1:{self.server_port}/v1/ocr"


class OCRHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        server = self.server
        assert self.path == "/v1/ocr"
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        server.calls.append(
            {"payload": payload, "authorization": self.headers.get("Authorization")}
        )
        body = (
            server.response.encode()
            if isinstance(server.response, str)
            else json.dumps(server.response).encode()
        )
        self.send_response(server.status)
        self.send_header(
            "Content-Type",
            "text/html" if isinstance(server.response, str) else "application/json",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


@contextmanager
def ocr_provider(app: Application):
    app.evidence.register_secret(OCR_KEY, PRIVATE_DOCUMENT)
    server = OCRProvider()
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        app.evidence.save_json("ocr-provider-requests.json", server.calls)


def configure_ocr(app: Application, server: OCRProvider):
    secret = app.request(
        "/api/system/secrets", "PUT", {"name": "MISTRAL_API_KEY", "value": OCR_KEY}
    )
    assert secret.status_code == 200, secret.text
    for key, value in (
        ("ingestion_ocr_endpoint", server.endpoint),
        ("ingestion_ocr_model", "mistral-ocr-4-0"),
    ):
        response = app.request(
            f"/api/system/settings/general/{key}", "PUT", {"value": value}
        )
        assert response.status_code == 200, response.text


async def run_import(app: Application, vault, *, options=None):
    from core.identity import LOCAL_USER_AUTHORITY, use_execution_authority
    from core.ingestion.import_service import ContentImportService
    from core.runtime.state import get_runtime_context

    with use_execution_authority(LOCAL_USER_AUTHORITY):
        service = ContentImportService(str(vault))
        submitted = service.submit(sources="local-fallback.pdf", options=options)
        await get_runtime_context().ingestion_worker.run_once()
        result = service.status(job_ids=submitted[0].job_id)[0]
    app.evidence.append(
        "ocr-import-results.jsonl",
        {
            "job_id": submitted[0].job_id,
            "status": result.status,
            "outputs": result.outputs,
        },
    )
    return submitted[0].job_id, result


def terminal_activity(app: Application, job_id: str, event: str):
    response = app.request(
        "/api/system/activity-log", params={"limit": 1000, "tag": "ingestion"}
    )
    assert response.status_code == 200
    return next(
        row["data"]
        for row in response.json()["entries"]
        if row.get("data", {}).get("job_id") == job_id
        and row["data"].get("event") == event
    )
